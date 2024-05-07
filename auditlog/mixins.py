from django import urls as urlresolvers
from django.conf import settings
from django.contrib import admin
from django.core.exceptions import FieldDoesNotExist
from django.forms.utils import pretty_name
from django.http import HttpRequest
from django.urls.exceptions import NoReverseMatch
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.utils.timezone import is_aware, localtime
from django.utils.translation import gettext_lazy as _

from auditlog.models import LogEntry
from auditlog.registry import auditlog
from auditlog.signals import accessed

MAX = 75


class LogEntryAdminMixin:
    request: HttpRequest

    @admin.display(description=_("Created"))
    def created(self, obj):
        if is_aware(obj.timestamp):
            return localtime(obj.timestamp)
        return obj.timestamp

    @admin.display(description=_("User"))
    def user_url(self, obj):
        return "system"

    @admin.display(description=_("Resource"))
    def resource_url(self, obj):
        app_label, model = obj.content_type.app_label, obj.content_type.model
        viewname = f"admin:{app_label}_{model}_change"
        try:
            args = [obj.record]
            link = urlresolvers.reverse(viewname, args=args)
        except NoReverseMatch:
            return obj.object_representation
        else:
            return format_html(
                '<a href="{}">{} - {}</a>',
                link,
                obj.content_type,
                obj.object_representation,
            )

    @admin.display(description=_("Changes"))
    def msg_short(self, obj):
        if obj.action in [LogEntry.Action.DELETE, LogEntry.Action.ACCESS]:
            return ""  # delete
        change_value = obj.changes_dict
        s = "" if len(change_value) == 1 else "s"
        fields = ", ".join(change_value.keys())
        if len(fields) > MAX:
            i = fields.rfind(" ", 0, MAX)
            fields = fields[:i] + " .."
        return "%d change%s: %s" % (len(change_value), s, fields)

    @admin.display(description=_("Changes"))
    def msg(self, obj):
        change_value = obj.changes_dict

        atom_changes = {}
        m2m_changes = {}

        for field, change_value in change_value.items():
            if isinstance(change_value, dict):
                assert (
                    change_value["type"] == "m2m"
                ), "Only m2m operations are expected to produce dict changes now"
                m2m_changes[field] = change_value
            else:
                atom_changes[field] = change_value

        msg = []

        if atom_changes:
            msg.append("<table>")
            msg.append(self._format_header("#", "Field", "From", "To"))
            for i, (field, change_value) in enumerate(sorted(atom_changes.items()), 1):
                value = [i, self.field_verbose_name(obj, field)] + (
                    ["***", "***"] if field == "password" else change_value
                )
                msg.append(self._format_line(*value))
            msg.append("</table>")

        if m2m_changes:
            msg.append("<table>")
            msg.append(self._format_header("#", "Relationship", "Action", "Objects"))
            for i, (field, change_value) in enumerate(sorted(m2m_changes.items()), 1):
                change_html = format_html_join(
                    mark_safe("<br>"),
                    "{}",
                    [(value,) for value in change_value["objects"]],
                )

                msg.append(
                    format_html(
                        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
                        i,
                        self.field_verbose_name(obj, field),
                        change_value["operation"],
                        change_html,
                    )
                )

            msg.append("</table>")

        return mark_safe("".join(msg))

    def _format_header(self, *labels):
        return format_html(
            "".join(["<tr>", "<th>{}</th>" * len(labels), "</tr>"]), *labels
        )

    def _format_line(self, *values):
        return format_html(
            "".join(["<tr>", "<td>{}</td>" * len(values), "</tr>"]), *values
        )

    def field_verbose_name(self, obj, field_name: str):
        model = obj.content_type.model_class()
        if model is None:
            return field_name
        try:
            model_fields = auditlog.get_model_fields(model._meta.model)
            mapping_field_name = model_fields["mapping_fields"].get(field_name)
            if mapping_field_name:
                return mapping_field_name
        except KeyError:
            # Model definition in auditlog was probably removed
            pass
        try:
            field = model._meta.get_field(field_name)
            return pretty_name(getattr(field, "verbose_name", field_name))
        except FieldDoesNotExist:
            return pretty_name(field_name)

    def _add_query_parameter(self, key: str, value: str):
        full_path = self.request.get_full_path()
        delimiter = "&" if "?" in full_path else "?"

        return f"{full_path}{delimiter}{key}={value}"


class LogAccessMixin:
    def render_to_response(self, context, **response_kwargs):
        obj = self.get_object()
        accessed.send(obj.__class__, instance=obj)
        return super().render_to_response(context, **response_kwargs)
