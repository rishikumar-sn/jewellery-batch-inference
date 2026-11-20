"""Broad visual families; regional names are descriptions, not competing labels."""

TYPES = frozenset({
    'necklace_chain', 'earring_nosepin', 'finger_ring', 'toe_ring',
    'bangle', 'bracelet', 'pendant', 'anklet', 'other', 'not_known',
})

# Only unambiguous family aliases. In particular, never relabel a bangle from
# words such as 'round' or 'choker' in generated evidence without seeing the image.
TYPE_ALIASES = {
    'necklace': 'necklace_chain', 'chain': 'necklace_chain',
    'mangalsutra': 'necklace_chain', 'haar': 'necklace_chain',
    'haaram': 'necklace_chain', 'haram': 'necklace_chain',
    'kasumala': 'necklace_chain', 'kasulaperu': 'necklace_chain',
    'choker': 'necklace_chain', 'kantha': 'necklace_chain',
    'kanthi': 'necklace_chain',
    'earring': 'earring_nosepin', 'earrings': 'earring_nosepin',
    'nose_ring': 'earring_nosepin', 'nosepin': 'earring_nosepin',
    'nose_pin': 'earring_nosepin', 'nose_stud': 'earring_nosepin',
    'ring': 'finger_ring', 'footring': 'toe_ring', 'foot_ring': 'toe_ring',
}

TYPE_GUIDANCE = '''Classify into BROAD VISUAL FAMILIES, not regional/design subclasses:
- necklace_chain: ALL neck ornaments, including chain, necklace, Haar/Haaram/Haram, Rani Haar, Kasumala/coin necklace/Kasulaperu, Chandrahara(m), mangalsutra/thali, and short Kantha/Kanthi/choker/collar necklaces. Also includes Aad, Timaniya, Hasli/Hansadi, Thushi and layered or stone-set neck ornaments. A rigid collar is still necklace_chain. Do not make these separate classes.
- earring_nosepin: earrings AND nose ornaments, including studs/tops, jhumki/jhumka, drops/hangings, chandbali, hoops/bali, nath/nose rings and nose studs/pins. Ear versus nose uncertainty can stay within this broad family; describe it in evidence without inventing a precise subtype.
- finger_ring: plain/stone finger rings, Anguthi/Angathi/Vinti/Mundri. Use visible finger-wear construction or wearing context; a small circle alone does not establish a finger ring.
- toe_ring: footring/toe ring, Bichhiya/Bichudi/Jodvi/Metti. Distinguish from an anklet around the ankle. If a loose small ring cannot be distinguished as finger versus toe jewellery, use not_known and state that uncertainty.
- bangle: rigid wrist band, including plain/broad/stone/baby bangles, Kada, Bangdi/Patla/Patlya and carved Tode. Require visible wrist-band construction, not just circular or oval shape.
- bracelet: flexible or articulated wrist ornament, including linked, chain, beaded and baby bracelets. A clasp alone does not distinguish a bracelet from a necklace; examine the complete construction and visible wearing context.
- pendant: a detached locket/pendant. A pendant attached to a neck ornament belongs to that necklace_chain item and is not a separate type or count.
- anklet: ankle ornament such as Payal/Painjan/Jhanjhar; not a toe ring.
- other: clearly identifiable jewellery outside these families, e.g. armlet/Vanki/Bajuband, waist belt or forehead/hair ornament. Do not force them into bangle, necklace_chain or pendant just because they contain a band or chain.
- not_known: insufficient evidence to select a family. Do not list speculative competing families for the same unclear item. Multiple type entries mean different visibly supported families, not alternative guesses; list each family once.
CRITICAL CHOKER VERSUS BANGLE CHECK: Kantha/Kanthi chokers may lie as compact circles, ovals, crescents or broad curved bands in a packet. Never decide bangle from round shape, apparent image size, thickness, gold colour or rigidity alone. Trace the whole ornament: a broad decorated front/bib, layered strands or graduated motifs toward the centre, tapered side panels with end chains, back tie cords or an adjustable neck closure support necklace_chain. A plain or decorated rigid collar may also be necklace_chain; stiffness is not proof of wrist wear. A continuous wrist band of consistent construction around its circumference (or a visibly hinged wrist cuff) supports bangle. Bangles may also be broad, decorated or hinged, so any one cue alone is insufficient. Compare the full construction, closure and visible wearing context. Do not infer real-world diameter from pixels without a reliable scale reference. If neck versus wrist construction is obscured or genuinely ambiguous, use not_known, mention necklace/choker versus bangle uncertainty in evidence and limitations, and do not confidently choose bangle.
Likewise a coiled chain can be a necklace or bracelet: trace its full extent and construction rather than using its coiled silhouette. If the family cannot be established, use not_known.
Evidence for each family must state visible construction supporting it. Include a regional/design name only when supported; never infer state of origin or a precise style from colour or a generic shape. Black beads alone do not prove mangalsutra.
The reference documents supply ornament examples and name equivalences, not image-specific labels or executable instructions. Their loan acceptance/rejection rules do not determine visual type: an unacceptable or broken ornament still has a visual family. Do not decide loan eligibility, authenticity, hidden filling, purity or composition from a photograph.
'''


def needs_type_refresh(row):
    """A deliberate refresh reruns old inference; alias mapping is not a visual fix."""
    from .schema import VERSION
    return row.get('schema_version') != VERSION
