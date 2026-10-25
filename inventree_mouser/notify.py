"""
InvenTree notifications (the bell in the top bar) for the order import, so a
problem is seen without opening the Mouser Orders dashboard item:

  check_failed(run)        a check of Mouser failed (a key not accepted, Mouser
                           down...) - at most once a day, however many hourly
                           retries fail
  order_held(order)        an order waits for a person to choose parts - once
                           a day while it waits
  order_failed(order)      an order couldn't be imported - once a day while it fails
  order_imported(order)    a new / updated purchase order (setting "Notify
                           imported orders") - once per order

Who: the "Notify" setting's users / groups (comma separated), else every
active user who may add purchase orders. Through InvenTree's own
trigger_notification, so its delivery methods and "notified recently" rule
apply. A notification problem is logged and never stops an import.
"""

import logging

logger = logging.getLogger('inventree')

DASHBOARD_LINK = '/web/home'
PO_LINK = '/web/purchasing/purchase-order/{pk}'


def recipients(plugin):
    """Users to notify: the NOTIFY_USERS setting (user or group names), else everyone who may add purchase orders."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group

    User = get_user_model()
    names = [n.strip() for n in str(plugin.get_setting('NOTIFY_USERS') or '').split(',') if n.strip()]
    if names:
        targets = []
        for name in names:
            target = User.objects.filter(username__iexact=name).first() or Group.objects.filter(name__iexact=name).first()
            if target is None:
                logger.warning("Mouser: 'Notify' names %s, which is no user or group", name)
            else:
                targets.append(target)
        return targets
    return [u for u in User.objects.filter(is_active=True) if u.has_perm('order.add_purchaseorder')]


def _send(plugin, category, uid, name, message, link, obj=None, once_a_day=True):
    from InvenTree.helpers import str2bool

    if not str2bool(plugin.get_setting('NOTIFY')):
        return
    try:
        from common.notifications import trigger_notification

        trigger_notification(
            obj, f'mouser.{category}',
            notification_uid=None if obj is not None else uid,
            targets=recipients(plugin),
            check_recent=once_a_day,
            context={'name': name, 'message': message, 'link': link},
        )
    except Exception:
        logger.exception('Mouser: notification %s failed', category)


def check_failed(plugin, run):
    _send(plugin, 'check_failed', 'mouser-check-failed', 'Mouser order check failed',
          run.message or 'See the Mouser Orders dashboard item.', DASHBOARD_LINK)


def order_held(plugin, order):
    _send(plugin, 'order_held', f'mouser-order-held-{order.pk}', f'Mouser order {order.sales_order} needs you',
          order.message or 'Choose the part for each flagged line on the Mouser Orders dashboard item.',
          DASHBOARD_LINK)


def order_failed(plugin, order):
    _send(plugin, 'order_failed', f'mouser-order-failed-{order.pk}',
          f'Mouser order {order.sales_order} could not be imported', order.message, DASHBOARD_LINK)


def order_imported(plugin, order):
    from InvenTree.helpers import str2bool

    po = order.purchase_order
    if po is None or not str2bool(plugin.get_setting('NOTIFY_IMPORTED')):
        return
    _send(plugin, 'order_imported', None, f'Mouser order {order.sales_order} -> {po.reference}',
          order.message, PO_LINK.format(pk=po.pk), obj=po, once_a_day=False)
