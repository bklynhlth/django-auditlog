import ast
import contextlib
import json
from copy import deepcopy
from datetime import timezone
from typing import Any, Callable, Dict, List, Union

from dateutil import parser
from dateutil.tz import gettz
from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core import serializers
from django.core.exceptions import (
    FieldDoesNotExist,
    ObjectDoesNotExist,
    ValidationError,
)
from django.db import DEFAULT_DB_ALIAS, models
from django.db.models import Q, QuerySet
from django.utils import formats
from django.utils import timezone as django_timezone
from django.utils.encoding import smart_str
from django.utils.translation import gettext_lazy as _

from auditlog.diff import mask_str

DEFAULT_OBJECT_REPR = "<error forming object repr>"


class LogEntryManager(models.Manager):
    """
    Custom manager for the :py:class:`LogEntry` model.
    """

    def log_create(self, instance, force_log: bool = False, **kwargs):
        """
        Helper method to create a new log entry. This method automatically populates some fields when no
        explicit value is given.

        :param instance: The model instance to log a change for.
        :type instance: Model
        :param force_log: Create a LogEntry even if no changes exist.
        :type force_log: bool
        :param kwargs: Field overrides for the :py:class:`LogEntry` object.
        :return: The new log entry or `None` if there were no changes.
        :rtype: LogEntry
        """

        change_value = kwargs.get("change_value", None)
        pk = self._get_pk_value(instance)
        table_name = self._get_table_name(instance)

        if change_value is not None or force_log:
            kwargs.setdefault(
                "content_type", ContentType.objects.get_for_model(instance)
            )
            kwargs.setdefault("identifier", pk)
            kwargs.setdefault("event_table", table_name)
            try:
                event_column = smart_str(instance)
            except ObjectDoesNotExist:
                event_column = DEFAULT_OBJECT_REPR
            kwargs.setdefault("event_column", event_column)

            # set correlation id
            return self.create(**kwargs)
        return None

    def log_m2m_changes(
        self, changed_queryset, instance, operation, field_name, **kwargs
    ):
        """Create a new "changed" log entry from m2m record.

        :param changed_queryset: The added or removed related objects.
        :type changed_queryset: QuerySet
        :param instance: The model instance to log a change for.
        :type instance: Model
        :param operation: "add" or "delete".
        :type action: str
        :param field_name: The name of the changed m2m field.
        :type field_name: str
        :param kwargs: Field overrides for the :py:class:`LogEntry` object.
        :return: The new log entry or `None` if there were no changes.
        :rtype: LogEntry
        """

        pk = self._get_pk_value(instance)
        table_name = self._get_table_name(instance)
        if changed_queryset:
            kwargs.setdefault(
                "content_type", ContentType.objects.get_for_model(instance)
            )
            kwargs.setdefault("identifier", pk)
            kwargs.setdefault("event_table", table_name)
            try:
                event_column = smart_str(instance)
            except ObjectDoesNotExist:
                event_column = DEFAULT_OBJECT_REPR
            kwargs.setdefault("event_column", event_column)
            kwargs.setdefault("action", LogEntry.Action.UPDATE)

            objects = [smart_str(instance) for instance in changed_queryset]
            kwargs["change_value"] = {
                field_name: {
                    "type": "m2m",
                    "operation": operation,
                    "objects": objects,
                }
            }

            return self.create(**kwargs)

        return None

    def get_for_object(self, instance):
        """
        Get log entries for the specified model instance.

        :param instance: The model instance to get log entries for.
        :type instance: Model
        :return: QuerySet of log entries for the given model instance.
        :rtype: QuerySet
        """
        # Return empty queryset if the given model instance is not a model instance.
        if not isinstance(instance, models.Model):
            return self.none()

        content_type = ContentType.objects.get_for_model(instance.__class__)
        pk = self._get_pk_value(instance)

        if isinstance(pk, int):
            return self.filter(content_type=content_type)
        else:
            return self.filter(content_type=content_type, identifier=smart_str(pk))

    def get_for_objects(self, queryset):
        """
        Get log entries for the objects in the specified queryset.

        :param queryset: The queryset to get the log entries for.
        :type queryset: QuerySet
        :return: The LogEntry objects for the objects in the given queryset.
        :rtype: QuerySet
        """
        if not isinstance(queryset, QuerySet) or queryset.count() == 0:
            return self.none()

        content_type = ContentType.objects.get_for_model(queryset.model)
        primary_keys = list(
            queryset.values_list(queryset.model._meta.pk.name, flat=True)
        )

        if isinstance(primary_keys[0], int):
            return self.filter(content_type=content_type).distinct()
        elif isinstance(queryset.model._meta.pk, models.UUIDField):
            primary_keys = [smart_str(pk) for pk in primary_keys]
            return (
                self.filter(content_type=content_type)
                .filter(Q(identifier__in=primary_keys))
                .distinct()
            )
        else:
            return (
                self.filter(content_type=content_type)
                .filter(Q(identifier__in=primary_keys))
                .distinct()
            )

    def get_for_model(self, model):
        """
        Get log entries for all objects of a specified type.

        :param model: The model to get log entries for.
        :type model: class
        :return: QuerySet of log entries for the given model.
        :rtype: QuerySet
        """
        # Return empty queryset if the given object is not valid.
        if not issubclass(model, models.Model):
            return self.none()

        content_type = ContentType.objects.get_for_model(model)

        return self.filter(content_type=content_type)

    def _get_pk_value(self, instance):
        """
        Get the primary key field value for a model instance.

        :param instance: The model instance to get the primary key for.
        :type instance: Model
        :return: The primary key value of the given model instance.
        """
        pk_field = instance._meta.pk.name
        pk = getattr(instance, pk_field, None)

        # Check to make sure that we got a pk not a model object.
        if isinstance(pk, models.Model):
            pk = self._get_pk_value(pk)
        return pk
    
    def _get_table_name(self, instance):
        """
        Get the table name for a model instance.
        :param instance: The model instance to get the table name for.
        :type instance: Model
        :return: The table name of the given model instance.
        """
        table_name = instance._meta.db_table
        return table_name

    def _get_copy_with_python_typed_fields(self, instance):
        """
        Attempt to create copy of instance and coerce types on instance fields

        The Django core serializer assumes that the values on object fields are
        correctly typed to their respective fields. Updates made to an object's
        in-memory state may not meet this assumption. To prevent this violation, values
        are typed by calling `to_python` from the field object, the result is set on a
        copy of the instance and the copy is sent to the serializer.
        """
        try:
            instance_copy = deepcopy(instance)
        except TypeError:
            instance_copy = instance
        for field in instance_copy._meta.fields:
            if not field.is_relation:
                value = getattr(instance_copy, field.name)
                try:
                    setattr(instance_copy, field.name, field.to_python(value))
                except ValidationError:
                    continue
        return instance_copy

    def _get_applicable_model_fields(
        self, instance, model_fields: Dict[str, List[str]]
    ) -> List[str]:
        include_fields = model_fields["include_fields"]
        exclude_fields = model_fields["exclude_fields"]
        all_field_names = [field.name for field in instance._meta.fields]

        if not include_fields and not exclude_fields:
            return all_field_names

        return list(set(include_fields or all_field_names).difference(exclude_fields))

    def _mask_serialized_fields(
        self, data: Dict[str, Any], mask_fields: List[str]
    ) -> Dict[str, Any]:
        all_field_data = data.pop("fields")

        masked_field_data = {}
        for key, value in all_field_data.items():
            if isinstance(value, str) and key in mask_fields:
                masked_field_data[key] = mask_str(value)
            else:
                masked_field_data[key] = value

        data["fields"] = masked_field_data
        return data


class LogEntry(models.Model):
    """
    Represents an entry in the audit log. The content type is saved along with the textual and numeric
    (if available) primary key, as well as the textual representation of the object when it was saved.
    It holds the action performed and the fields that were changed in the transaction.

    If AuditlogMiddleware is used, the actor will be set automatically. Keep in mind that
    editing / re-saving LogEntry instances may set the actor to a wrong value - editing LogEntry
    instances is not recommended (and it should not be necessary).
    """

    class Action:
        """
        The actions that Auditlog distinguishes: creating, updating and deleting objects. Viewing objects
        is not logged. The values of the actions are numeric, a higher integer value means a more intrusive
        action. This may be useful in some cases when comparing actions because the ``__lt``, ``__lte``,
        ``__gt``, ``__gte`` lookup filters can be used in queries.

        The valid actions are :py:attr:`Action.CREATE`, :py:attr:`Action.UPDATE`,
        :py:attr:`Action.DELETE` and :py:attr:`Action.ACCESS`.
        """

        CREATE = 'create'
        UPDATE = 'update'
        DELETE = 'delete'
        ACCESS = 'access'

        choices = (
            (CREATE, _("create")),
            (UPDATE, _("update")),
            (DELETE, _("delete")),
            (ACCESS, _("access")),
        )

    class Reason:
        data_entry = "data_entry"
        data_processing = "data_processing"
        data_deletion = "data_deletion"
        data_cleaning = "data_cleaning"

        choices = (
            (data_entry, _("data_entry")),
            (data_processing, _("data_processing")),
            (data_deletion, _("data_deletion")),
            (data_cleaning, _("data_cleaning")),
        )
    source = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="+",
        verbose_name=_("source"),
    )
    database_name = models.CharField(
        max_length=255, verbose_name=_("database name"), default=settings.DATABASE_NAME
    )
    identifier = models.CharField(
        db_index=True, max_length=255, verbose_name=_("identifier"), default="None"
    )
    event_table = models.CharField(
        max_length=255, verbose_name=_("event table"), default="None"
    )
    event_type = models.CharField(
        max_length=255,
        choices=Reason.choices,
        verbose_name=_("event_type"),
        null=True,
        blank=True,
    )
    event_column = models.TextField(verbose_name=_("event column"), default="None")
    action = models.CharField(
        max_length=255, choices=Action.choices, verbose_name=_("action"), db_index=True
    )
    change_value = models.JSONField(null=True, verbose_name=_("change message"))
    timestamp = models.DateTimeField(
        default=django_timezone.now,
        db_index=True,
        verbose_name=_("timestamp"),
    )
    content_type = models.ForeignKey(
        to="contenttypes.ContentType",
        on_delete=models.CASCADE,
        related_name="+",
        verbose_name=_("content type"),
    )

    objects = LogEntryManager()

    class Meta:
        get_latest_by = "timestamp"
        ordering = ["-timestamp"]
        verbose_name = _("log entry")
        verbose_name_plural = _("log entries")

    def __str__(self):
        if self.action == self.Action.CREATE:
            fstring = _("Created {repr:s}")
        elif self.action == self.Action.UPDATE:
            fstring = _("Updated {repr:s}")
        elif self.action == self.Action.DELETE:
            fstring = _("Deleted {repr:s}")
        else:
            fstring = _("Logged {repr:s}")

        return fstring.format(repr=self.event_column)

    @property
    def changes_dict(self):
        """
        :return: The changes recorded in this log entry as a dictionary object.
        """
        return changes_func(self)

    @property
    def changes_str(self, colon=": ", arrow=" \u2192 ", separator="; "):
        """
        Return the changes recorded in this log entry as a string. The formatting of the string can be
        customized by setting alternate values for colon, arrow and separator. If the formatting is still
        not satisfying, please use :py:func:`LogEntry.changes_dict` and format the string yourself.

        :param colon: The string to place between the field name and the values.
        :param arrow: The string to place between each old and new value.
        :param separator: The string to place between each field.
        :return: A readable string of the changes in this log entry.
        """
        substrings = []

        for field, values in self.changes_dict.items():
            substring = "{field_name:s}{colon:s}{old:s}{arrow:s}{new:s}".format(
                field_name=field,
                colon=colon,
                old=values[0],
                arrow=arrow,
                new=values[1],
            )
            substrings.append(substring)

        return separator.join(substrings)

    @property
    def changes_display_dict(self):
        """
        :return: The changes recorded in this log entry intended for display to users as a dictionary object.
        """
        # Get the model and model_fields
        from auditlog.registry import auditlog

        model = self.content_type.model_class()
        model_fields = auditlog.get_model_fields(model._meta.model)
        changes_display_dict = {}
        # grab the changes_dict and iterate through
        for field_name, values in self.changes_dict.items():
            # try to get the field attribute on the model
            try:
                field = model._meta.get_field(field_name)
            except FieldDoesNotExist:
                changes_display_dict[field_name] = values
                continue
            values_display = []
            # handle choices fields and Postgres ArrayField to get human-readable version
            choices_dict = None
            if getattr(field, "choices", []):
                choices_dict = dict(field.choices)
            if getattr(getattr(field, "base_field", None), "choices", []):
                choices_dict = dict(field.base_field.choices)

            if choices_dict:
                for value in values:
                    try:
                        value = ast.literal_eval(value)
                        if type(value) is [].__class__:
                            values_display.append(
                                ", ".join(
                                    [choices_dict.get(val, "None") for val in value]
                                )
                            )
                        else:
                            values_display.append(choices_dict.get(value, "None"))
                    except Exception:
                        values_display.append(choices_dict.get(value, "None"))
            else:
                try:
                    field_type = field.get_internal_type()
                except AttributeError:
                    # if the field is a relationship it has no internal type and exclude it
                    continue
                for value in values:
                    # handle case where field is a datetime, date, or time type
                    if field_type in ["DateTimeField", "DateField", "TimeField"]:
                        try:
                            value = parser.parse(value)
                            if field_type == "DateField":
                                value = value.date()
                            elif field_type == "TimeField":
                                value = value.time()
                            elif field_type == "DateTimeField":
                                value = value.replace(tzinfo=timezone.utc)
                                value = value.astimezone(gettz(settings.TIME_ZONE))
                            value = formats.localize(value)
                        except ValueError:
                            pass
                    elif field_type in ["ForeignKey", "OneToOneField"]:
                        value = self._get_changes_display_for_fk_field(field, value)

                    # check if length is longer than 140 and truncate with ellipsis
                    if len(value) > 140:
                        value = f"{value[:140]}..."

                    values_display.append(value)
            verbose_name = model_fields["mapping_fields"].get(
                field.name, getattr(field, "verbose_name", field.name)
            )
            changes_display_dict[verbose_name] = values_display
        return changes_display_dict

    def _get_changes_display_for_fk_field(
        self, field: Union[models.ForeignKey, models.OneToOneField], value: Any
    ) -> str:
        """
        :return: A string representing a given FK value and the field to which it belongs
        """
        # Return "None" if the FK value is "None".
        if value == "None":
            return value

        # Attempt to convert given value to the PK type for the related model
        try:
            pk_value = field.related_model._meta.pk.to_python(value)
        # ValidationError will handle legacy values where string representations were
        # stored rather than PKs. This will also handle cases where the PK type is
        # changed between the time the LogEntry is created and this method is called.
        except ValidationError:
            return value
        # Attempt to return the string representation of the object
        try:
            return smart_str(field.related_model.objects.get(pk=pk_value))
        # ObjectDoesNotExist will be raised if the object was deleted.
        except ObjectDoesNotExist:
            return f"Deleted '{field.related_model.__name__}' ({value})"


class AuditlogHistoryField(GenericRelation):
    """
    A subclass of py:class:`django.contrib.contenttypes.fields.GenericRelation` that sets some default
    variables. This makes it easier to access Auditlog's log entries, for example in templates.

    By default, this field will assume that your primary keys are numeric, simply because this is the most
    common case. However, if you have a non-integer primary key, you can simply pass ``pk_indexable=False``
    to the constructor, and Auditlog will fall back to using a non-indexed text based field for this model.

    Using this field will not automatically register the model for automatic logging. This is done so you
    can be more flexible with how you use this field.

    :param pk_indexable: Whether the primary key for this model is not an :py:class:`int` or :py:class:`long`.
    :type pk_indexable: bool
    :param delete_related: Delete referenced auditlog entries together with the tracked object.
        Defaults to False to keep the integrity of the auditlog.
    :type delete_related: bool
    """

    def __init__(self, pk_indexable=True, delete_related=False, **kwargs):
        kwargs["to"] = LogEntry

        kwargs["content_type_field"] = "content_type"
        self.delete_related = delete_related
        super().__init__(**kwargs)

    def bulk_related_objects(self, objs, using=DEFAULT_DB_ALIAS):
        """
        Return all objects related to ``objs`` via this ``GenericRelation``.
        """
        if self.delete_related:
            return super().bulk_related_objects(objs, using)

        # When deleting, Collector.collect() finds related objects using this
        # method.  However, because we don't want to delete these related
        # objects, we simply return an empty list.
        return []


# should I add a signal receiver for setting_changed?
changes_func = None


def _changes_func() -> Callable[[LogEntry], Dict]:
    def json_then_text(instance: LogEntry) -> Dict:
        if instance.change_value:
            return instance.change_value
        return {}

    def default(instance: LogEntry) -> Dict:
        return instance.change_value or {}

    if settings.AUDITLOG_USE_TEXT_CHANGES_IF_JSON_IS_NOT_PRESENT:
        return json_then_text
    return default
