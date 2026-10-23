"""
Mouser in InvenTree's own "Import from Supplier" wizard (Parts -> Add Parts
-> Import from Supplier): search Mouser, pick a category, check parameters,
create the part with its manufacturer part, Mouser supplier part and price
breaks. SupplierMixin adds the "Supplier" setting (the Company that is
Mouser in your InvenTree).

The same import_part / import_manufacturer_part / import_supplier_part
methods are used by the order import (order_import.py) and by other plugins
(OMG Harness Import's pending parts), so a part is created the same way
whichever route it comes in by.

Moved from the OMG Harness Import plugin (mouser_supplier.py) unchanged in
behaviour, apart from the setting names and the parameter hook (hooks.py).
"""

import io
import logging

from django.db.models import Q

from company.models import Company, ManufacturerPart, SupplierPart, SupplierPriceBreak
from part.models import Part
from plugin.base.supplier import helpers as supplier
from plugin.base.supplier.mixins import SupplierMixin

from mouser_lookup import search_by_mpn

from . import hooks

logger = logging.getLogger('inventree')

# InvenTree's own downloader has no browser User-Agent and a 2.5 s timeout.
# Mouser's image server sits behind bot protection that refuses non-browser
# clients, and judges the whole request - with only a User-Agent it answered
# with an HTML page. These are what download_image() below uses.
IMAGE_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/126.0 Safari/537.36'
)
IMAGE_BROWSER_HEADERS = {
    'User-Agent': IMAGE_USER_AGENT,
    'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
    'Accept-Language': 'en-AU,en;q=0.9',
    'Referer': 'https://www.mouser.com/',
    'Sec-Fetch-Dest': 'image',
    'Sec-Fetch-Mode': 'no-cors',
    'Sec-Fetch-Site': 'same-origin',
}
IMAGE_TIMEOUT_SECONDS = 10
IMAGE_MAX_BYTES = 5 * 1024 * 1024


def usable_image_url(value):
    """The image URL, or '' if there isn't a real one ("None", "null", relative...)."""
    url = (value or '').strip()
    if url.lower() in ('', 'none', 'null'):
        return ''
    if url.startswith('//'):
        url = 'https:' + url
    return url if url.lower().startswith(('http://', 'https://')) else ''


def _is_public_host(hostname):
    """True only if every address the host resolves to is a public one."""
    import ipaddress
    import socket
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        addr = ipaddress.ip_address(info[4][0].split('%')[0])
        if (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_reserved or addr.is_unspecified):
            return False
    return bool(infos)


def _describe_non_image(response, url, content_type):
    """Why a download wasn't an image, with what the server sent back (e.g. a bot-protection page title)."""
    import re
    snippet = ''
    try:
        body = response.raw.read(65536, decode_content=True) or b''
        text = body.decode('utf-8', errors='replace')
        title = re.search(r'<title[^>]*>(.*?)</title>', text, re.I | re.S)
        snippet = (title.group(1) if title else re.sub(r'<[^>]+>', ' ', text))
        snippet = ' '.join(snippet.split())[:160]
    except Exception:
        pass
    blocked = any(word in snippet.lower() for word in ('access denied', 'denied', 'forbidden', 'captcha', 'robot', 'blocked'))
    return (
        f"Mouser sent back a {content_type or 'non-image'} response, not an image "
        f"(status {response.status_code}, final URL {url}"
        + (f", page says: '{snippet}'" if snippet else '')
        + ').'
        + (" This looks like Mouser's bot protection blocking this server." if blocked else '')
    )


def _fetch_image(url):
    """
    Download an image with full browser headers - when InvenTree's own
    downloader is refused. Same kinds of checks InvenTree applies: http(s)
    only, public hosts only (checked again after every redirect), a size cap,
    and the bytes must be a real image. Returns a PIL Image; raises otherwise.
    """
    from urllib.parse import urljoin, urlparse

    import requests
    from PIL import Image

    for _hop in range(5):
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError(f'Not a web address: {url}')
        if not _is_public_host(parsed.hostname):
            raise ValueError(f'Refusing to download from a non-public address: {parsed.hostname}')
        response = requests.get(url, headers=dict(IMAGE_BROWSER_HEADERS), timeout=IMAGE_TIMEOUT_SECONDS,
                                stream=True, allow_redirects=False)
        if response.is_redirect or response.is_permanent_redirect:
            url = urljoin(url, response.headers.get('Location', ''))
            response.close()
            continue
        break
    else:
        raise ValueError('Too many redirects')

    content_type = response.headers.get('Content-Type', '')
    if response.status_code != 200 or (content_type and not content_type.lower().startswith('image/')):
        raise ValueError(_describe_non_image(response, url, content_type))
    if int(response.headers.get('Content-Length') or 0) > IMAGE_MAX_BYTES:
        raise ValueError('Image is larger than the size limit')

    data = bytearray()
    for chunk in response.iter_content(chunk_size=65536):
        data.extend(chunk)
        if len(data) > IMAGE_MAX_BYTES:
            raise ValueError('Image is larger than the size limit')

    Image.open(io.BytesIO(data)).verify()
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def existing_part_for_listing(r):
    """
    The InvenTree part this exact Mouser listing already maps to, or None:
    a SupplierPart with this Mouser part number as its SKU, else a
    ManufacturerPart with this MPN from this manufacturer (MPN alone isn't
    enough - two manufacturers can share one).
    """
    spn = (r.get('spn') or '').strip()
    if spn:
        sp = SupplierPart.objects.filter(SKU__iexact=spn).select_related('part').first()
        if sp:
            return sp.part
    manufacturer = (r.get('manufacturer') or '').strip()
    mp_qs = ManufacturerPart.objects.filter(MPN__iexact=r['mpn'])
    if manufacturer:
        mp_qs = mp_qs.filter(manufacturer__name__iexact=manufacturer)
    mp = mp_qs.select_related('part').first()
    return mp.part if mp else None


def existing_purchaseable_part(mpn):
    """The purchaseable part import_part() would reuse for this MPN (named or IPN'd with it), or None."""
    mpn = (mpn or '').strip()
    if not mpn:
        return None
    return Part.objects.filter(Q(name__iexact=mpn) | Q(IPN__iexact=mpn), purchaseable=True).first()


def _log_image_error(part, url):
    """A failed image download in Settings -> System -> Error Logs as well as the server log. Call from an except block."""
    logger.warning('Failed to download/save Mouser image for part %s from %s', part.pk, url, exc_info=True)
    from InvenTree.exceptions import log_error
    log_error(path=f'mouser: image for part {part.pk} ({part.name}) from {url}', plugin='mouser')


class MouserSupplierMixin(SupplierMixin):
    """SupplierMixin for Mouser - see https://docs.inventree.org/en/stable/plugins/mixins/supplier/"""

    def download_image(self, img_url):
        """
        (ContentFile, format), like SupplierMixin.download_image(): InvenTree's
        downloader with a browser User-Agent first, then _fetch_image() with the
        full set of browser headers if Mouser's bot protection refuses it.
        """
        from django.core.files.base import ContentFile
        from InvenTree.helpers_model import download_image_from_url

        try:
            img = download_image_from_url(img_url, timeout=IMAGE_TIMEOUT_SECONDS, user_agent=IMAGE_USER_AGENT,
                                          max_size=IMAGE_MAX_BYTES)
        except Exception as exc:
            logger.info("Mouser image via InvenTree's downloader failed (%s) - retrying with browser headers", exc)
            img = _fetch_image(img_url)

        fmt = img.format or 'PNG'
        buffer = io.BytesIO()
        img.save(buffer, format=fmt)
        return ContentFile(buffer.getvalue()), fmt

    def get_suppliers(self):
        return [supplier.Supplier(slug='mouser', name='Mouser Electronics')]

    def search_key(self):
        return self.get_setting('SEARCH_API_KEY') or ''

    def get_search_results(self, supplier_slug, term):
        api_key = self.search_key()
        if not api_key:
            return []
        try:
            results = search_by_mpn(term, api_key)
        except Exception:
            return []

        # Each result is keyed by its MOUSER part number (spn), not the MPN:
        # Mouser often lists one MPN several times (cut tape vs reel, or two
        # manufacturers using the same number), and the spn is unique per
        # listing - it's what import_supplier_part() stores as the SKU.
        term_key = term.strip().lower()
        search_results = []
        for r in results:
            spn = (r.get('spn') or '').strip()
            manufacturer = (r.get('manufacturer') or '').strip()
            search_results.append(supplier.SearchResult(
                sku=spn or r['mpn'],
                name=r['mpn'],
                description=' — '.join(x for x in (manufacturer, r.get('description') or '') if x),
                exact=term_key in (r['mpn'].strip().lower(), spn.lower()),
                price=(f"{r['price_breaks'][0]['price']:.2f} {r['price_breaks'][0]['currency']}"
                       if r.get('price_breaks') else None),
                link=r.get('url') or '',
                image_url=r.get('image') or '',
                existing_part=existing_part_for_listing(r),
            ))
        return search_results

    def get_import_data(self, supplier_slug, part_id):
        """
        part_id is normally the Mouser part number (one listing); an MPN works
        too. Match order: the listing whose Mouser part number is part_id, then
        the first whose MPN is part_id, then the only result.
        """
        api_key = self.search_key()
        results = search_by_mpn(part_id, api_key) if api_key else []
        key = part_id.strip().lower()
        by_spn = [r for r in results if (r.get('spn') or '').strip().lower() == key]
        if by_spn:
            return by_spn[0]
        by_mpn = [r for r in results if r['mpn'].strip().lower() == key]
        if by_mpn:
            return by_mpn[0]
        if len(results) == 1:
            return results[0]
        raise supplier.PartNotFoundError()

    def get_pricing_data(self, data):
        return {pb['quantity']: (pb['price'], pb['currency']) for pb in data.get('price_breaks', [])}

    def get_parameters(self, data):
        """
        Parameters from other plugins first (hooks.mouser_import_parameters -
        e.g. OMG Harness Import's Component Type, gauges, colours), then
        Mouser's own attributes. A Mouser attribute naming the same template as
        a provider's parameter is dropped, so the provider's value is offered.
        """
        params, seen = [], set()
        for name, value in hooks.import_parameters(data):
            params.append(supplier.ImportParameter(name=name, value=value))
            seen.add(name.strip().lower())
        for name, value in (data.get('attributes') or {}).items():
            if value and name.strip().lower() not in seen:
                params.append(supplier.ImportParameter(name=name, value=value))
        return params

    def import_part(self, data, *, category, creation_user):
        """
        The part named by its MPN (an existing purchaseable part with that name
        or IPN is reused, never duplicated). Downloads Mouser's image when the
        part has none and "Download part images" is on.
        """
        part = existing_purchaseable_part(data['mpn'])
        if not part:
            part = Part.objects.create(
                name=data['mpn'],
                description=(data.get('description') or '')[:250],
                link=data.get('url') or '',
                category=category,
                purchaseable=True,
                active=True,
                virtual=False,
            )

        image_url = usable_image_url(data.get('image'))
        if not part.image and image_url and self.get_setting('DOWNLOAD_IMAGES', False):
            try:
                file, fmt = self.download_image(image_url)
                part.image.save(f'part_{part.pk}_image.{fmt.lower()}', file)
            except Exception:
                # A failed image never blocks the import, but the reason is findable.
                _log_image_error(part, image_url)
        return part

    def import_manufacturer_part(self, data, *, part):
        manufacturer_name = data.get('manufacturer') or 'Unknown'
        mfr, _ = Company.objects.get_or_create(
            name__iexact=manufacturer_name,
            defaults={'name': manufacturer_name, 'is_manufacturer': True, 'is_supplier': False},
        )
        mfr_part, _ = ManufacturerPart.objects.get_or_create(MPN=data['mpn'], manufacturer=mfr, part=part)
        return mfr_part

    def import_supplier_part(self, data, *, part, manufacturer_part):
        """
        The part's Mouser supplier part for this listing - found by part,
        supplier and SKU (what InvenTree keeps unique), so one entered by hand
        without a manufacturer part is reused, never duplicated (that failed
        InvenTree's unique check). A missing manufacturer part / link is filled
        in; one already set is left alone.
        """
        supplier_part, created = SupplierPart.objects.get_or_create(
            SKU=data.get('spn') or data['mpn'],
            supplier=self.supplier_company,
            part=part,
            defaults={'link': data.get('url') or '', 'manufacturer_part': manufacturer_part},
        )
        if not created:
            changed = []
            if manufacturer_part is not None and supplier_part.manufacturer_part_id is None:
                supplier_part.manufacturer_part = manufacturer_part
                changed.append('manufacturer_part')
            if data.get('url') and not supplier_part.link:
                supplier_part.link = data['url']
                changed.append('link')
            if changed:
                supplier_part.save()
        if data.get('price_breaks'):
            SupplierPriceBreak.objects.filter(part=supplier_part).delete()
            SupplierPriceBreak.objects.bulk_create([
                SupplierPriceBreak(part=supplier_part, quantity=pb['quantity'],
                                   price=pb['price'], price_currency=pb['currency'])
                for pb in data['price_breaks']
            ])
        return supplier_part
