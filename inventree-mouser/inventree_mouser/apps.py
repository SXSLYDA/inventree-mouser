"""Django app configuration for the Mouser plugin (loaded through InvenTree's AppMixin)."""

from django.apps import AppConfig


class InvenTreeMouserConfig(AppConfig):
    name = 'inventree_mouser'
    label = 'inventree_mouser'
    verbose_name = 'Mouser Integration'
    default_auto_field = 'django.db.models.AutoField'
