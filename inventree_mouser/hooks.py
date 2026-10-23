"""
Hooks other plugins can provide, so this plugin stays generic while a
plugin like OMG Harness Import adds its own part data. A hook is just a
method of that name on any ACTIVE plugin; nothing needs registering.

  mouser_import_parameters(data) -> [(template name, value), ...]
      Parameters to offer on the Parameters step of InvenTree's Import from
      Supplier wizard for a Mouser listing (data = a mouser_lookup search
      result). Offered first; a Mouser attribute naming the same template
      is dropped, so the provider's value wins.

  mouser_part_created(part, data, source) -> [note, ...] or None
      A part was just created from Mouser data outside the wizard. source is
      "order" (the order import). Called after the purchase order is saved
      and committed (outside its lock - a provider may make network calls),
      and only for parts really created, never an existing part the import
      reused. Return notes for anything that couldn't be set; they are added
      to the order's message. The wizard doesn't call this - its own
      Parameters step already covers it.

A hook that raises is logged and skipped; it never stops an import.
"""

import logging

logger = logging.getLogger('inventree')


def providers(hook_name):
    """(plugin, bound method) for every active plugin that has hook_name."""
    from plugin.registry import registry

    found = []
    for slug, plugin in list(registry.plugins.items()):
        method = getattr(plugin, hook_name, None)
        if not callable(method):
            continue
        if registry.get_plugin(slug) is None:  # inactive
            continue
        found.append((plugin, method))
    return found


def import_parameters(data):
    """[(name, value)] from every mouser_import_parameters provider, in order."""
    params = []
    for plugin, method in providers('mouser_import_parameters'):
        try:
            params.extend((str(name), str(value)) for name, value in (method(data) or []) if name and value)
        except Exception:
            logger.exception('Mouser: %s.mouser_import_parameters failed', plugin.slug)
    return params


def part_created(part, data, source):
    """Tell every mouser_part_created provider. Returns ["plugin: problem"] for any that failed."""
    problems = []
    for plugin, method in providers('mouser_part_created'):
        try:
            for note in method(part, data, source) or []:
                problems.append(f'{plugin.slug}: {note}')
        except Exception as exc:
            logger.exception('Mouser: %s.mouser_part_created failed for part %s', plugin.slug, part.pk)
            problems.append(f'{plugin.slug}: {exc}')
    return problems
