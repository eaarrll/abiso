"""CAP 1.2 and Atom feed parsing for the PAGASA public-alert feed.

PAGASA specifics observed July 2026 (verified live):
- Feed entries link CAP XML under output/{gfa,wcp,acp,tcp}/{uuid}.cap
- <geocode> carries SAME values that are 9-digit (old-format) PSGC
  province codes, e.g. 175200000 = Oriental Mindoro
- <polygon> present alongside geocodes; areas can cover partial provinces
- msgType Update/Cancel reference earlier identifiers via <references>
"""

from dataclasses import dataclass, field

from lxml import etree

CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"
ATOM_NS = "http://www.w3.org/2005/Atom"


@dataclass
class CapArea:
    area_desc: str
    polygons: list[list[tuple[float, float]]] = field(default_factory=list)  # (lat, lon)
    geocodes: list[tuple[str, str]] = field(default_factory=list)  # (valueName, value)


@dataclass
class CapInfo:
    event: str
    severity: str
    urgency: str
    certainty: str
    language: str = ""
    effective: str = ""
    expires: str = ""
    headline: str = ""
    description: str = ""
    instruction: str = ""
    parameters: list[tuple[str, str]] = field(default_factory=list)
    areas: list[CapArea] = field(default_factory=list)


@dataclass
class CapAlert:
    identifier: str
    sender: str
    sent: str
    status: str
    msg_type: str
    references: str = ""
    infos: list[CapInfo] = field(default_factory=list)


def _text(el, tag: str) -> str:
    node = el.find(f"{{{CAP_NS}}}{tag}")
    return (node.text or "").strip() if node is not None and node.text else ""


def _parse_polygon(raw: str) -> list[tuple[float, float]]:
    """CAP polygon: whitespace-separated 'lat,lon' pairs."""
    pts = []
    for pair in raw.split():
        lat_s, _, lon_s = pair.partition(",")
        try:
            pts.append((float(lat_s), float(lon_s)))
        except ValueError:
            return []
    return pts


def parse_cap(xml: bytes) -> CapAlert:
    root = etree.fromstring(xml)
    alert = CapAlert(
        identifier=_text(root, "identifier"),
        sender=_text(root, "sender"),
        sent=_text(root, "sent"),
        status=_text(root, "status"),
        msg_type=_text(root, "msgType"),
        references=_text(root, "references"),
    )
    for info_el in root.findall(f"{{{CAP_NS}}}info"):
        info = CapInfo(
            event=_text(info_el, "event"),
            severity=_text(info_el, "severity"),
            urgency=_text(info_el, "urgency"),
            certainty=_text(info_el, "certainty"),
            language=_text(info_el, "language"),
            effective=_text(info_el, "effective"),
            expires=_text(info_el, "expires"),
            headline=_text(info_el, "headline"),
            description=_text(info_el, "description"),
            instruction=_text(info_el, "instruction"),
        )
        for p_el in info_el.findall(f"{{{CAP_NS}}}parameter"):
            info.parameters.append((_text(p_el, "valueName"), _text(p_el, "value")))
        for a_el in info_el.findall(f"{{{CAP_NS}}}area"):
            area = CapArea(area_desc=_text(a_el, "areaDesc"))
            for poly_el in a_el.findall(f"{{{CAP_NS}}}polygon"):
                pts = _parse_polygon(poly_el.text or "")
                if pts:
                    area.polygons.append(pts)
            for g_el in a_el.findall(f"{{{CAP_NS}}}geocode"):
                area.geocodes.append((_text(g_el, "valueName"), _text(g_el, "value")))
            info.areas.append(area)
        alert.infos.append(info)
    return alert


@dataclass
class FeedEntry:
    entry_id: str
    updated: str
    title: str
    cap_url: str


def parse_atom(xml: bytes) -> list[FeedEntry]:
    root = etree.fromstring(xml)
    entries = []
    for e in root.findall(f"{{{ATOM_NS}}}entry"):
        cap_url = ""
        for link in e.findall(f"{{{ATOM_NS}}}link"):
            href = link.get("href", "")
            if href.endswith(".cap") or link.get("type") == "application/cap+xml":
                cap_url = href
                break
        id_el = e.find(f"{{{ATOM_NS}}}id")
        upd_el = e.find(f"{{{ATOM_NS}}}updated")
        title_el = e.find(f"{{{ATOM_NS}}}title")
        entries.append(
            FeedEntry(
                entry_id=(id_el.text or "").strip() if id_el is not None else "",
                updated=(upd_el.text or "").strip() if upd_el is not None else "",
                title=(title_el.text or "").strip() if title_el is not None else "",
                cap_url=cap_url,
            )
        )
    return entries
