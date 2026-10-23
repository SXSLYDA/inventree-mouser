"""
The Mouser orders this plugin has seen, and how each line was matched.

One MouserOrder per Mouser sales order - that row is what stops an order
being imported twice. Its lines are kept even after the purchase order is
made, so the review list can show what was matched to what.

These tables have real migrations (migrations/), applied by
`invoke update` / `manage.py migrate` like InvenTree's own.
"""

from django.db import models


class MouserOrder(models.Model):
    """One Mouser order and where it is in the import."""

    STATE_HELD = 'held'            # a line needs a person - nothing created yet
    STATE_IMPORTED = 'imported'    # a new purchase order was made
    STATE_LINKED = 'linked'        # matched to a purchase order already in InvenTree
    STATE_IGNORED = 'ignored'      # cancelled on Mouser, before the start date, or dismissed
    STATE_FAILED = 'failed'        # an error - see message; tried again on the next run
    STATES = [
        (STATE_HELD, 'Needs review'),
        (STATE_IMPORTED, 'Imported'),
        (STATE_LINKED, 'Linked to existing PO'),
        (STATE_IGNORED, 'Ignored'),
        (STATE_FAILED, 'Failed'),
    ]

    sales_order = models.CharField(max_length=40, unique=True, help_text='Mouser sales order number')
    web_order = models.CharField(max_length=40, blank=True)
    po_number = models.CharField(max_length=64, blank=True, help_text='PO number typed at Mouser checkout')
    order_date = models.DateField(null=True, blank=True)
    mouser_status = models.CharField(max_length=60, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    buyer = models.CharField(max_length=120, blank=True)
    detail = models.JSONField(default=dict, blank=True, help_text="Mouser's order as fetched (normalized)")

    state = models.CharField(max_length=10, choices=STATES, default=STATE_HELD)
    message = models.TextField(blank=True)
    purchase_order = models.ForeignKey('order.PurchaseOrder', null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name='+')
    source = models.CharField(max_length=20, blank=True, help_text='schedule / manual / by-number')
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'inventree_mouser'
        ordering = ['-order_date', '-pk']
        verbose_name = 'Mouser order'

    def __str__(self):
        return f'Mouser {self.sales_order} ({self.get_state_display()})'


class MouserOrderLine(models.Model):
    """One line of a Mouser order and the InvenTree part it goes to."""

    MATCH_SUPPLIER_PART = 'supplier_part'  # a Mouser supplier part with this SKU already exists
    MATCH_PART = 'part'                    # exactly one InvenTree part - a Mouser supplier part is added to it
    MATCH_NEW = 'new'                      # nothing like it - the part is created from Mouser
    MATCH_POSSIBLE = 'possible'            # something close (packaging / variant suffix) - a person decides
    MATCH_AMBIGUOUS = 'ambiguous'          # several exact matches - a person picks
    MATCHES = [
        (MATCH_SUPPLIER_PART, 'Mouser supplier part'),
        (MATCH_PART, 'Existing part'),
        (MATCH_NEW, 'New part'),
        (MATCH_POSSIBLE, 'Possible match'),
        (MATCH_AMBIGUOUS, 'Several matches'),
    ]

    DECISION_AUTO = ''          # use the match as found
    DECISION_LINK = 'link'      # use part (chosen by a person)
    DECISION_CREATE = 'create'  # create a new part from Mouser
    DECISION_SKIP = 'skip'      # leave this line off the purchase order
    DECISIONS = [
        (DECISION_AUTO, 'As matched'),
        (DECISION_LINK, 'Use chosen part'),
        (DECISION_CREATE, 'Create new part'),
        (DECISION_SKIP, 'Leave off the PO'),
    ]

    order = models.ForeignKey(MouserOrder, on_delete=models.CASCADE, related_name='lines')
    line_no = models.PositiveIntegerField(default=0)
    mouser_pn = models.CharField(max_length=100, blank=True)
    mpn = models.CharField(max_length=100, blank=True)
    manufacturer = models.CharField(max_length=120, blank=True)
    description = models.CharField(max_length=250, blank=True)
    customer_pn = models.CharField(max_length=100, blank=True)
    quantity = models.PositiveIntegerField(default=0)
    unit_price = models.DecimalField(max_digits=19, decimal_places=6, null=True, blank=True)

    match = models.CharField(max_length=15, choices=MATCHES, blank=True)
    candidates = models.JSONField(default=list, blank=True, help_text='Part pks a person can choose from')
    decision = models.CharField(max_length=10, choices=DECISIONS, blank=True, default=DECISION_AUTO)
    part = models.ForeignKey('part.Part', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    supplier_part = models.ForeignKey('company.SupplierPart', null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name='+')
    message = models.TextField(blank=True)

    class Meta:
        app_label = 'inventree_mouser'
        ordering = ['order', 'line_no']
        verbose_name = 'Mouser order line'

    def __str__(self):
        return f'{self.order.sales_order} line {self.line_no}: {self.mouser_pn} x {self.quantity}'

    @property
    def needs_person(self):
        """True while this line holds its order: unresolved possible / ambiguous match."""
        if self.decision in (self.DECISION_LINK, self.DECISION_CREATE, self.DECISION_SKIP):
            return self.decision == self.DECISION_LINK and self.part_id is None
        return self.match in (self.MATCH_POSSIBLE, self.MATCH_AMBIGUOUS, '')


class ImportRun(models.Model):
    """One check of Mouser for new orders (scheduled or from the dashboard) - shown on the dashboard item."""

    started = models.DateTimeField(auto_now_add=True)
    finished = models.DateTimeField(null=True, blank=True)
    ok = models.BooleanField(null=True, blank=True)
    source = models.CharField(max_length=20, blank=True, help_text='schedule / manual')
    message = models.TextField(blank=True)
    counts = models.JSONField(default=dict, blank=True)

    class Meta:
        app_label = 'inventree_mouser'
        ordering = ['-started']
        verbose_name = 'Mouser order check'

    def __str__(self):
        return f'Mouser order check {self.started:%Y-%m-%d %H:%M} ({self.source})'
