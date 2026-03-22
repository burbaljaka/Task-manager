from django import template

from basic_app.kanban_utils import status_css_slug as status_css_slug_impl

register = template.Library()


@register.filter
def status_css_slug(value: str) -> str:
    # Same algorithm as statusCssSlug() in tasks.html (see kanban_utils.status_css_slug).
    return status_css_slug_impl(value)
