from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any


from spark_intelligence.memory.retention_policy import (
    ACTIVE_STATE_REVALIDATION_DAYS_BY_PREDICATE as _ACTIVE_STATE_REVALIDATION_DAYS_BY_PREDICATE,
)

_CITY_PATTERNS = [
    re.compile(r"\bi\s+moved\s+to\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
    re.compile(r"\bi\s+live\s+in\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
    re.compile(r"\bi(?:'m| am)\s+in\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
]
_STARTUP_PATTERNS = [
    re.compile(r"\bmy\s+startup\s+is\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
    re.compile(r"\b([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})\s+is\s+my\s+startup", re.I),
    re.compile(r"\bi\s+created\s+a\s+startup\s+called\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
    re.compile(r"\bi\s+run\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
]
_FOUNDER_PATTERNS = [
    re.compile(r"\bi\s+founded\s+a\s+startup\s+called\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
    re.compile(r"\bi(?:'m| am)\s+the\s+founder\s+of\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
    re.compile(r"\bi\s+founded\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
    re.compile(r"\bi\s+started\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
    re.compile(r"\bi\s+built\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
    re.compile(r"\bi\s+launched\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
]
_HACK_PATTERNS = [
    re.compile(r"\bwe\s+were\s+hacked\s+by\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
]
_MISSION_PATTERNS = [
    re.compile(r"\bi(?:'m| am)\s+trying\s+to\s+([A-Za-z][A-Za-z0-9\s\-'`.&,]{3,120})", re.I),
    re.compile(r"\bi(?:'m| am)\s+(rebuilding\s+after\s+the\s+hack|reviving\s+the\s+companies)(?:[.!?,]|$)", re.I),
]
_SPARK_ROLE_PATTERNS = [
    re.compile(
        r"\bspark\s+(?:is\s+going\s+to\s+be|will\s+be)\s+(?:an\s+)?important\s+part\s+of\s+(?:this\s+|the\s+)?rebuild(?:[.!?,]|$)",
        re.I,
    ),
]
_OCCUPATION_PATTERNS = [
    re.compile(r"\bi(?:'m| am)\s+an\s+(entrepreneur)(?:\s+(?:now|today|currently))?(?:[.!?,]|$)", re.I),
]
_NAME_PATTERNS = [
    re.compile(r"\bmy\s+name\s+is\s+([a-z][a-z\s\-'.`]{0,40})", re.I),
    re.compile(r"\bcall\s+me\s+([a-z][a-z\s\-'.`]{0,40})", re.I),
]
_COUNTRY_PATTERNS = [
    re.compile(r"\bmy\s+country\s+is\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
    re.compile(r"\bi(?:'m| am)\s+from\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
    re.compile(r"\bi(?:'m| am)\s+based\s+in\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
    re.compile(r"\bi(?:'m| am)\s+based\s+out\s+of\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
]
_COUNTRY_IN_PATTERNS = [
    re.compile(r"\bi(?:'m| am)\s+in\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
]
_COUNTRY_LIVE_PATTERNS = [
    re.compile(r"\bi\s+live\s+in\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
]
_COUNTRY_MOVE_PATTERNS = [
    re.compile(r"\bi\s+moved\s+to\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
]
_TIMEZONE_PATTERNS = [
    re.compile(r"\bmy\s+timezone\s+is\s+([A-Za-z_]+/[A-Za-z_]+(?:/[A-Za-z_]+)?)", re.I),
    re.compile(r"\bi(?:'m| am)\s+in\s+timezone\s+([A-Za-z_]+/[A-Za-z_]+(?:/[A-Za-z_]+)?)", re.I),
    re.compile(r"\bi(?:'m| am)\s+on\s+(utc[+-]\d{1,2}(?::\d{2})?)", re.I),
    re.compile(r"\bmy\s+timezone\s+is\s+(utc[+-]\d{1,2}(?::\d{2})?)", re.I),
]
_STOP_WORDS = {"and", "but", "because", "so", "that", "which", "where"}
_LOWERCASE_JOINERS = {"and", "of", "the", "de", "al", "bin"}
_TEMPORAL_TAIL_WORDS = {"now", "today", "currently"}
_KNOWN_COUNTRY_NAMES = {
    "afghanistan": "Afghanistan",
    "albania": "Albania",
    "algeria": "Algeria",
    "andorra": "Andorra",
    "angola": "Angola",
    "antigua and barbuda": "Antigua and Barbuda",
    "argentina": "Argentina",
    "armenia": "Armenia",
    "australia": "Australia",
    "austria": "Austria",
    "azerbaijan": "Azerbaijan",
    "bahamas": "Bahamas",
    "bahrain": "Bahrain",
    "bangladesh": "Bangladesh",
    "barbados": "Barbados",
    "belarus": "Belarus",
    "belgium": "Belgium",
    "belize": "Belize",
    "benin": "Benin",
    "bhutan": "Bhutan",
    "bolivia": "Bolivia",
    "bosnia and herzegovina": "Bosnia and Herzegovina",
    "botswana": "Botswana",
    "brazil": "Brazil",
    "brunei": "Brunei",
    "bulgaria": "Bulgaria",
    "burkina faso": "Burkina Faso",
    "burundi": "Burundi",
    "cabo verde": "Cabo Verde",
    "cambodia": "Cambodia",
    "cameroon": "Cameroon",
    "canada": "Canada",
    "central african republic": "Central African Republic",
    "chad": "Chad",
    "chile": "Chile",
    "china": "China",
    "colombia": "Colombia",
    "comoros": "Comoros",
    "congo": "Congo",
    "costa rica": "Costa Rica",
    "cote d'ivoire": "Cote d'Ivoire",
    "croatia": "Croatia",
    "cuba": "Cuba",
    "cyprus": "Cyprus",
    "czech republic": "Czech Republic",
    "czechia": "Czechia",
    "democratic republic of the congo": "Democratic Republic of the Congo",
    "denmark": "Denmark",
    "djibouti": "Djibouti",
    "dominica": "Dominica",
    "dominican republic": "Dominican Republic",
    "ecuador": "Ecuador",
    "egypt": "Egypt",
    "el salvador": "El Salvador",
    "equatorial guinea": "Equatorial Guinea",
    "eritrea": "Eritrea",
    "estonia": "Estonia",
    "eswatini": "Eswatini",
    "ethiopia": "Ethiopia",
    "fiji": "Fiji",
    "finland": "Finland",
    "france": "France",
    "gabon": "Gabon",
    "gambia": "Gambia",
    "georgia": "Georgia",
    "germany": "Germany",
    "ghana": "Ghana",
    "greece": "Greece",
    "grenada": "Grenada",
    "guatemala": "Guatemala",
    "guinea": "Guinea",
    "guinea-bissau": "Guinea-Bissau",
    "guyana": "Guyana",
    "haiti": "Haiti",
    "honduras": "Honduras",
    "hungary": "Hungary",
    "iceland": "Iceland",
    "india": "India",
    "indonesia": "Indonesia",
    "iran": "Iran",
    "iraq": "Iraq",
    "ireland": "Ireland",
    "israel": "Israel",
    "italy": "Italy",
    "jamaica": "Jamaica",
    "japan": "Japan",
    "jordan": "Jordan",
    "kazakhstan": "Kazakhstan",
    "kenya": "Kenya",
    "kiribati": "Kiribati",
    "kuwait": "Kuwait",
    "kyrgyzstan": "Kyrgyzstan",
    "laos": "Laos",
    "latvia": "Latvia",
    "lebanon": "Lebanon",
    "lesotho": "Lesotho",
    "liberia": "Liberia",
    "libya": "Libya",
    "liechtenstein": "Liechtenstein",
    "lithuania": "Lithuania",
    "luxembourg": "Luxembourg",
    "madagascar": "Madagascar",
    "malawi": "Malawi",
    "malaysia": "Malaysia",
    "maldives": "Maldives",
    "mali": "Mali",
    "malta": "Malta",
    "marshall islands": "Marshall Islands",
    "mauritania": "Mauritania",
    "mauritius": "Mauritius",
    "mexico": "Mexico",
    "micronesia": "Micronesia",
    "moldova": "Moldova",
    "monaco": "Monaco",
    "mongolia": "Mongolia",
    "montenegro": "Montenegro",
    "morocco": "Morocco",
    "mozambique": "Mozambique",
    "myanmar": "Myanmar",
    "namibia": "Namibia",
    "nauru": "Nauru",
    "nepal": "Nepal",
    "netherlands": "Netherlands",
    "new zealand": "New Zealand",
    "nicaragua": "Nicaragua",
    "niger": "Niger",
    "nigeria": "Nigeria",
    "north korea": "North Korea",
    "north macedonia": "North Macedonia",
    "norway": "Norway",
    "oman": "Oman",
    "pakistan": "Pakistan",
    "palau": "Palau",
    "palestine": "Palestine",
    "panama": "Panama",
    "papua new guinea": "Papua New Guinea",
    "paraguay": "Paraguay",
    "peru": "Peru",
    "philippines": "Philippines",
    "poland": "Poland",
    "portugal": "Portugal",
    "qatar": "Qatar",
    "romania": "Romania",
    "russia": "Russia",
    "rwanda": "Rwanda",
    "saint kitts and nevis": "Saint Kitts and Nevis",
    "saint lucia": "Saint Lucia",
    "saint vincent and the grenadines": "Saint Vincent and the Grenadines",
    "samoa": "Samoa",
    "san marino": "San Marino",
    "sao tome and principe": "Sao Tome and Principe",
    "saudi arabia": "Saudi Arabia",
    "senegal": "Senegal",
    "serbia": "Serbia",
    "seychelles": "Seychelles",
    "sierra leone": "Sierra Leone",
    "singapore": "Singapore",
    "slovakia": "Slovakia",
    "slovenia": "Slovenia",
    "solomon islands": "Solomon Islands",
    "somalia": "Somalia",
    "south africa": "South Africa",
    "south korea": "South Korea",
    "south sudan": "South Sudan",
    "spain": "Spain",
    "sri lanka": "Sri Lanka",
    "sudan": "Sudan",
    "suriname": "Suriname",
    "sweden": "Sweden",
    "switzerland": "Switzerland",
    "syria": "Syria",
    "tajikistan": "Tajikistan",
    "tanzania": "Tanzania",
    "thailand": "Thailand",
    "timor-leste": "Timor-Leste",
    "togo": "Togo",
    "tonga": "Tonga",
    "trinidad and tobago": "Trinidad and Tobago",
    "tunisia": "Tunisia",
    "turkey": "Turkey",
    "turkmenistan": "Turkmenistan",
    "tuvalu": "Tuvalu",
    "uganda": "Uganda",
    "ukraine": "Ukraine",
    "united arab emirates": "United Arab Emirates",
    "united kingdom": "United Kingdom",
    "united states": "United States",
    "uruguay": "Uruguay",
    "uzbekistan": "Uzbekistan",
    "vanuatu": "Vanuatu",
    "vatican city": "Vatican City",
    "venezuela": "Venezuela",
    "vietnam": "Vietnam",
    "yemen": "Yemen",
    "zambia": "Zambia",
    "zimbabwe": "Zimbabwe",
    "uae": "UAE",
    "uae.": "UAE",
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "us": "United States",
    "u.s.": "United States",
    "usa": "United States",
    "u.s.a.": "United States",
}


@dataclass(frozen=True)
class ProfileFactObservation:
    predicate: str
    value: str
    operation: str
    evidence_text: str
    fact_name: str


@dataclass(frozen=True)
class ProfileFactQuery:
    predicate: str | None
    fact_name: str
    label: str
    query_kind: str = "single_fact"
    predicate_prefix: str | None = None


_GENERIC_PROFILE_QUERY_SPECS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("profile.cofounder_name", "profile_cofounder_name", "cofounder", ("cofounder",)),
    ("profile.mentor_name", "profile_mentor_name", "mentor", ("mentor",)),
    ("profile.manager_name", "profile_manager_name", "manager", ("manager",)),
    ("profile.assistant_name", "profile_assistant_name", "assistant", ("assistant",)),
    ("profile.partner_name", "profile_partner_name", "partner", ("partner", "wife", "husband")),
    ("profile.mother_name", "profile_mother_name", "mother", ("mother",)),
    ("profile.father_name", "profile_father_name", "father", ("father",)),
    ("profile.sister_name", "profile_sister_name", "sister", ("sister",)),
    ("profile.brother_name", "profile_brother_name", "brother", ("brother",)),
)


def _history_fact_query(
    *,
    predicate: str,
    fact_name: str,
    label: str,
    query_kind: str,
) -> ProfileFactQuery:
    return ProfileFactQuery(
        predicate=predicate,
        fact_name=fact_name,
        label=label,
        query_kind=query_kind,
    )


def _generic_profile_fact_query(
    *,
    predicate: str,
    fact_name: str,
    label: str,
    query_kind: str = "single_fact",
) -> ProfileFactQuery:
    return ProfileFactQuery(
        predicate=predicate,
        fact_name=fact_name,
        label=label,
        query_kind=query_kind,
    )


def _match_generic_profile_history_query(text: str) -> ProfileFactQuery | None:
    for predicate, fact_name, label, aliases in _GENERIC_PROFILE_QUERY_SPECS:
        if any(
            phrase in text
            for alias in aliases
            for phrase in (
                f"who was my {alias} before",
                f"what was my previous {alias}",
                f"what {alias} did you have for me before",
            )
        ):
            return _generic_profile_fact_query(
                predicate=predicate,
                fact_name=fact_name,
                label=label,
                query_kind="fact_history",
            )
    return None


def _match_generic_profile_event_history_query(text: str) -> ProfileFactQuery | None:
    for predicate, fact_name, label, aliases in _GENERIC_PROFILE_QUERY_SPECS:
        if any(
            phrase in text
            for alias in aliases
            for phrase in (
                f"what memory events do you have about my {alias}",
                f"show my {alias} history",
                f"what {alias} history do you have for me",
            )
        ):
            return _generic_profile_fact_query(
                predicate=predicate,
                fact_name=fact_name,
                label=label,
                query_kind="event_history",
            )
    return None


def _match_generic_profile_current_query(text: str) -> ProfileFactQuery | None:
    for predicate, fact_name, label, aliases in _GENERIC_PROFILE_QUERY_SPECS:
        if any(
            phrase in text
            for alias in aliases
            for phrase in (
                f"who is my {alias}",
                f"what is my {alias} name",
                f"what's my {alias} name",
                f"what {alias} do you have for me",
            )
        ):
            return _generic_profile_fact_query(
                predicate=predicate,
                fact_name=fact_name,
                label=label,
            )
    return None


def _detect_profile_fact_history_query(text: str) -> ProfileFactQuery | None:
    if any(
        phrase in text
        for phrase in (
            "where did i live before",
            "where was i living before",
            "what city did i live in before",
            "which city did i live in before",
            "what was my previous city",
            "what city was i in before",
            "what city did you have for me before",
        )
    ):
        return _history_fact_query(
            predicate="profile.city",
            fact_name="profile_city",
            label="city",
            query_kind="fact_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what country did i live in before",
            "which country did i live in before",
            "what was my previous country",
            "what country did you have for me before",
        )
    ):
        return _history_fact_query(
            predicate="profile.home_country",
            fact_name="profile_home_country",
            label="country",
            query_kind="fact_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what timezone did i have before",
            "what was my previous timezone",
            "which timezone did you have for me before",
        )
    ):
        return _history_fact_query(
            predicate="profile.timezone",
            fact_name="profile_timezone",
            label="timezone",
            query_kind="fact_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what startup did i have before",
            "what was my previous startup",
        )
    ):
        return _history_fact_query(
            predicate="profile.startup_name",
            fact_name="profile_startup_name",
            label="startup",
            query_kind="fact_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what company did i found before",
            "what was the previous company i founded",
        )
    ):
        return _history_fact_query(
            predicate="profile.founder_of",
            fact_name="profile_founder_of",
            label="company you founded",
            query_kind="fact_history",
        )
    generic_history_query = _match_generic_profile_history_query(text)
    if generic_history_query is not None:
        return generic_history_query
    if any(
        phrase in text
        for phrase in (
            "what was my previous plan",
            "what plan did i have before",
            "what was our previous plan",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_plan",
            fact_name="profile_current_plan",
            label="current plan",
            query_kind="fact_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what was my previous focus",
            "what was our previous priority",
            "what priority did we have before",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_focus",
            fact_name="profile_current_focus",
            label="current focus",
            query_kind="fact_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what was my previous decision",
            "what was our previous decision",
            "what did we decide before",
            "what decision did we make before",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_decision",
            fact_name="profile_current_decision",
            label="current decision",
            query_kind="fact_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what was my previous blocker",
            "what was our previous blocker",
            "what was our previous bottleneck",
            "what were we blocked on before",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_blocker",
            fact_name="profile_current_blocker",
            label="current blocker",
            query_kind="fact_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what was my previous status",
            "what was our previous status",
            "what was the project status before",
            "what status did we have before",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_status",
            fact_name="profile_current_status",
            label="current status",
            query_kind="fact_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what was my previous commitment",
            "what was our previous commitment",
            "what did we commit to before",
            "what was the commitment before",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_commitment",
            fact_name="profile_current_commitment",
            label="current commitment",
            query_kind="fact_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what was my previous milestone",
            "what was our previous milestone",
            "what was the milestone before",
            "what milestone did we have before",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_milestone",
            fact_name="profile_current_milestone",
            label="current milestone",
            query_kind="fact_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what was my previous risk",
            "what was our previous risk",
            "what was the risk before",
            "what risk did we have before",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_risk",
            fact_name="profile_current_risk",
            label="current risk",
            query_kind="fact_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what was my previous dependency",
            "what was our previous dependency",
            "what was the dependency before",
            "what dependency did we have before",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_dependency",
            fact_name="profile_current_dependency",
            label="current dependency",
            query_kind="fact_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what was my previous constraint",
            "what was our previous constraint",
            "what was the constraint before",
            "what constraint did we have before",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_constraint",
            fact_name="profile_current_constraint",
            label="current constraint",
            query_kind="fact_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what was my previous assumption",
            "what was our previous assumption",
            "what was the assumption before",
            "what assumption did we have before",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_assumption",
            fact_name="profile_current_assumption",
            label="current assumption",
            query_kind="fact_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what was my previous owner",
            "what was our previous owner",
            "what was the owner before",
            "who owned this before",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_owner",
            fact_name="profile_current_owner",
            label="current owner",
            query_kind="fact_history",
        )
    return None


def _detect_profile_fact_event_history_query(text: str) -> ProfileFactQuery | None:
    if any(
        phrase in text
        for phrase in (
            "what memory events do you have about where i live",
            "what memory events do you have about my city",
            "what memory events do you have about my location",
            "show my city history",
            "show my location history",
        )
    ):
        return _history_fact_query(
            predicate="profile.city",
            fact_name="profile_city",
            label="city",
            query_kind="event_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what memory events do you have about my country",
            "show my country history",
            "what country history do you have for me",
        )
    ):
        return _history_fact_query(
            predicate="profile.home_country",
            fact_name="profile_home_country",
            label="country",
            query_kind="event_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what memory events do you have about my timezone",
            "show my timezone history",
        )
    ):
        return _history_fact_query(
            predicate="profile.timezone",
            fact_name="profile_timezone",
            label="timezone",
            query_kind="event_history",
        )
    generic_event_history_query = _match_generic_profile_event_history_query(text)
    if generic_event_history_query is not None:
        return generic_event_history_query
    if any(
        phrase in text
        for phrase in (
            "what memory events do you have about my current plan",
            "show my plan history",
            "what plan history do you have for me",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_plan",
            fact_name="profile_current_plan",
            label="current plan",
            query_kind="event_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what memory events do you have about my current focus",
            "what memory events do you have about our priority",
            "show my focus history",
            "show our priority history",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_focus",
            fact_name="profile_current_focus",
            label="current focus",
            query_kind="event_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what memory events do you have about my current decision",
            "what memory events do you have about our decision",
            "show my decision history",
            "show our decision history",
            "what decision history do you have for me",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_decision",
            fact_name="profile_current_decision",
            label="current decision",
            query_kind="event_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what memory events do you have about my current blocker",
            "what memory events do you have about our blocker",
            "what memory events do you have about our bottleneck",
            "show my blocker history",
            "show our blocker history",
            "show our bottleneck history",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_blocker",
            fact_name="profile_current_blocker",
            label="current blocker",
            query_kind="event_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what memory events do you have about my current status",
            "what memory events do you have about our status",
            "what memory events do you have about the project status",
            "show my status history",
            "show our status history",
            "show the project status history",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_status",
            fact_name="profile_current_status",
            label="current status",
            query_kind="event_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what memory events do you have about my current commitment",
            "what memory events do you have about our commitment",
            "show my commitment history",
            "show our commitment history",
            "show the commitment history",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_commitment",
            fact_name="profile_current_commitment",
            label="current commitment",
            query_kind="event_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what memory events do you have about my current milestone",
            "what memory events do you have about our milestone",
            "show my milestone history",
            "show our milestone history",
            "show the milestone history",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_milestone",
            fact_name="profile_current_milestone",
            label="current milestone",
            query_kind="event_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what memory events do you have about my current risk",
            "what memory events do you have about our risk",
            "show my risk history",
            "show our risk history",
            "show the risk history",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_risk",
            fact_name="profile_current_risk",
            label="current risk",
            query_kind="event_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what memory events do you have about my current dependency",
            "what memory events do you have about our dependency",
            "show my dependency history",
            "show our dependency history",
            "show the dependency history",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_dependency",
            fact_name="profile_current_dependency",
            label="current dependency",
            query_kind="event_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what memory events do you have about my current constraint",
            "what memory events do you have about our constraint",
            "show my constraint history",
            "show our constraint history",
            "show the constraint history",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_constraint",
            fact_name="profile_current_constraint",
            label="current constraint",
            query_kind="event_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what memory events do you have about my current assumption",
            "what memory events do you have about our assumption",
            "show my assumption history",
            "show our assumption history",
            "show the assumption history",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_assumption",
            fact_name="profile_current_assumption",
            label="current assumption",
            query_kind="event_history",
        )
    if any(
        phrase in text
        for phrase in (
            "what memory events do you have about my current owner",
            "what memory events do you have about our owner",
            "show my owner history",
            "show our owner history",
            "show the owner history",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_owner",
            fact_name="profile_current_owner",
            label="current owner",
            query_kind="event_history",
        )
    return None


def detect_profile_fact_observation(user_message: str) -> ProfileFactObservation | None:
    text = str(user_message or "").strip()
    if not text:
        return None
    if "?" in text and detect_profile_fact_query(text) is not None:
        return None
    preferred_name = _extract_name(text)
    if preferred_name:
        return ProfileFactObservation(
            predicate="profile.preferred_name",
            value=preferred_name,
            operation="update",
            evidence_text=text,
            fact_name="profile_preferred_name",
        )
    country = _extract_country(text)
    if country:
        return ProfileFactObservation(
            predicate="profile.home_country",
            value=country,
            operation="update",
            evidence_text=text,
            fact_name="profile_home_country",
        )
    timezone = _extract_timezone(text)
    if timezone:
        return ProfileFactObservation(
            predicate="profile.timezone",
            value=timezone,
            operation="update",
            evidence_text=text,
            fact_name="profile_timezone",
        )
    occupation = _extract_occupation(text)
    if occupation:
        return ProfileFactObservation(
            predicate="profile.occupation",
            value=occupation,
            operation="update",
            evidence_text=text,
            fact_name="profile_occupation",
        )
    startup = _extract_startup(text)
    if startup:
        return ProfileFactObservation(
            predicate="profile.startup_name",
            value=startup,
            operation="update",
            evidence_text=text,
            fact_name="profile_startup_name",
        )
    founder_of = _extract_founder_of(text)
    if founder_of:
        return ProfileFactObservation(
            predicate="profile.founder_of",
            value=founder_of,
            operation="update",
            evidence_text=text,
            fact_name="profile_founder_of",
        )
    hack_actor = _extract_hack_actor(text)
    if hack_actor:
        return ProfileFactObservation(
            predicate="profile.hack_actor",
            value=hack_actor,
            operation="update",
            evidence_text=text,
            fact_name="profile_hack_actor",
        )
    current_mission = _extract_current_mission(text)
    if current_mission:
        return ProfileFactObservation(
            predicate="profile.current_mission",
            value=current_mission,
            operation="update",
            evidence_text=text,
            fact_name="profile_current_mission",
        )
    spark_role = _extract_spark_role(text)
    if spark_role:
        return ProfileFactObservation(
            predicate="profile.spark_role",
            value=spark_role,
            operation="update",
            evidence_text=text,
            fact_name="profile_spark_role",
        )
    city = _extract_city(text)
    if not city:
        return None
    return ProfileFactObservation(
        predicate="profile.city",
        value=city,
        operation="update",
        evidence_text=text,
        fact_name="profile_city",
    )


def detect_profile_fact_query(user_message: str) -> ProfileFactQuery | None:
    text = str(user_message or "").strip().lower()
    if not text:
        return None
    normalized_question = re.sub(r"[.!?]+$", "", text).strip()
    historical_query = _detect_profile_fact_history_query(text)
    if historical_query is not None:
        return historical_query
    event_history_query = _detect_profile_fact_event_history_query(text)
    if event_history_query is not None:
        return event_history_query
    if any(
        phrase in text
        for phrase in (
            "how do you know",
            "why do you think",
            "what makes you think",
        )
    ):
        if any(
            phrase in text
            for phrase in (
                "where i live",
                "what city i live in",
                "what city do i live in",
                "what city am i in",
                "my city",
            )
        ):
            return ProfileFactQuery(
                predicate="profile.city",
                fact_name="profile_city",
                label="city",
                query_kind="fact_explanation",
            )
        if any(
            phrase in text
            for phrase in (
                "what country i live in",
                "what country do i live in",
                "what country am i in",
                "my country",
            )
        ):
            return ProfileFactQuery(
                predicate="profile.home_country",
                fact_name="profile_home_country",
                label="country",
                query_kind="fact_explanation",
            )
        if any(
            phrase in text
            for phrase in (
                "my startup",
                "what startup i created",
                "what is my startup",
                "what's my startup",
            )
        ):
            return ProfileFactQuery(
                predicate="profile.startup_name",
                fact_name="profile_startup_name",
                label="startup",
                query_kind="fact_explanation",
            )
        if any(
            phrase in text
            for phrase in (
                "what company i founded",
                "what company did i found",
                "what have i founded",
            )
        ):
            return ProfileFactQuery(
                predicate="profile.founder_of",
                fact_name="profile_founder_of",
                label="company you founded",
                query_kind="fact_explanation",
            )
        if any(
            phrase in text
            for phrase in (
                "my occupation",
                "what i do",
                "what am i",
            )
        ):
            return ProfileFactQuery(
                predicate="profile.occupation",
                fact_name="profile_occupation",
                label="occupation",
                query_kind="fact_explanation",
            )
        if "my name" in text:
            return ProfileFactQuery(
                predicate="profile.preferred_name",
                fact_name="profile_preferred_name",
                label="name",
                query_kind="fact_explanation",
            )
        if "my timezone" in text:
            return ProfileFactQuery(
                predicate="profile.timezone",
                fact_name="profile_timezone",
                label="timezone",
                query_kind="fact_explanation",
            )
        if any(
            phrase in text
            for phrase in (
                "what i'm trying to do now",
                "what i am trying to do now",
                "my mission right now",
                "what i'm doing now",
                "what i am doing now",
            )
        ):
            return ProfileFactQuery(
                predicate="profile.current_mission",
                fact_name="profile_current_mission",
                label="current mission",
                query_kind="fact_explanation",
            )
    if any(
        phrase in text
        for phrase in (
            "what name do you have for me",
            "what name do you have saved for me",
            "which name do you have for me",
            "what's my name",
            "what is my name",
        )
    ):
        return ProfileFactQuery(predicate="profile.preferred_name", fact_name="profile_preferred_name", label="name")
    if any(
        phrase in text
        for phrase in (
            "what startup do you have for me",
            "what startup do you have saved for me",
            "what is my startup",
            "what's my startup",
        )
    ):
        return ProfileFactQuery(predicate="profile.startup_name", fact_name="profile_startup_name", label="startup")
    if any(
        phrase in text
        for phrase in (
            "what happened to us",
            "who hacked us",
            "who hacked me",
            "who hacked the company",
        )
    ):
        return ProfileFactQuery(predicate="profile.hack_actor", fact_name="profile_hack_actor", label="hack actor")
    if any(
        phrase in text
        for phrase in (
            "what am i trying to do now",
            "what am i doing now",
            "what is my mission right now",
            "what's my mission right now",
        )
    ):
        return ProfileFactQuery(
            predicate="profile.current_mission",
            fact_name="profile_current_mission",
            label="current mission",
        )
    generic_current_query = _match_generic_profile_current_query(text)
    if generic_current_query is not None:
        return generic_current_query
    if any(
        phrase in text
        for phrase in (
            "what is my current plan",
            "what's my current plan",
            "what is our plan",
            "what's our plan",
            "what is the plan",
            "what's the plan",
            "what do you have as my current plan",
            "what plan do you have for me",
            "what are we planning",
            "what do we plan to do",
        )
    ):
        return ProfileFactQuery(
            predicate="profile.current_plan",
            fact_name="profile_current_plan",
            label="current plan",
        )
    if any(
        phrase in text
        for phrase in (
            "what is my current focus",
            "what's my current focus",
            "what is our priority",
            "what's our priority",
            "what are we focusing on",
            "what do you have as my current focus",
        )
    ):
        return ProfileFactQuery(
            predicate="profile.current_focus",
            fact_name="profile_current_focus",
            label="current focus",
        )
    if any(
        phrase in text
        for phrase in (
            "what is my current decision",
            "what's my current decision",
            "what is our current decision",
            "what's our current decision",
            "what did we decide",
            "what have we decided",
            "what decision did we make",
            "what are we going with",
        )
    ):
        return ProfileFactQuery(
            predicate="profile.current_decision",
            fact_name="profile_current_decision",
            label="current decision",
        )
    if any(
        phrase in text
        for phrase in (
            "what is my current blocker",
            "what's my current blocker",
            "what is our current blocker",
            "what's our current blocker",
            "what is our bottleneck",
            "what's our bottleneck",
            "what are we blocked on",
        )
    ):
        return ProfileFactQuery(
            predicate="profile.current_blocker",
            fact_name="profile_current_blocker",
            label="current blocker",
        )
    if any(
        phrase in text
        for phrase in (
            "what is my current status",
            "what's my current status",
            "what is our current status",
            "what's our current status",
            "what is my status",
            "what's my status",
            "what is our status",
            "what's our status",
            "what is the project status",
            "what's the project status",
            "where do things stand",
        )
    ):
        return ProfileFactQuery(
            predicate="profile.current_status",
            fact_name="profile_current_status",
            label="current status",
        )
    if any(
        phrase in text
        for phrase in (
            "what is my current commitment",
            "what's my current commitment",
            "what is our current commitment",
            "what's our current commitment",
            "what is our commitment",
            "what's our commitment",
            "what did we commit to",
            "what commitment do we have",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_commitment",
            fact_name="profile_current_commitment",
            label="current commitment",
        )
    if any(
        phrase in text
        for phrase in (
            "what is my current milestone",
            "what's my current milestone",
            "what is our current milestone",
            "what's our current milestone",
            "what is our milestone",
            "what's our milestone",
            "what is the milestone",
            "what's the milestone",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_milestone",
            fact_name="profile_current_milestone",
            label="current milestone",
        )
    if any(
        phrase in text
        for phrase in (
            "what is my current risk",
            "what's my current risk",
            "what is our current risk",
            "what's our current risk",
            "what is our risk",
            "what's our risk",
            "what is the risk",
            "what's the risk",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_risk",
            fact_name="profile_current_risk",
            label="current risk",
        )
    if any(
        phrase in text
        for phrase in (
            "what is my current dependency",
            "what's my current dependency",
            "what is our current dependency",
            "what's our current dependency",
            "what is our dependency",
            "what's our dependency",
            "what is the dependency",
            "what's the dependency",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_dependency",
            fact_name="profile_current_dependency",
            label="current dependency",
        )
    if any(
        phrase in text
        for phrase in (
            "what is my current constraint",
            "what's my current constraint",
            "what is our current constraint",
            "what's our current constraint",
            "what is our constraint",
            "what's our constraint",
            "what is the constraint",
            "what's the constraint",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_constraint",
            fact_name="profile_current_constraint",
            label="current constraint",
        )
    if any(
        phrase in text
        for phrase in (
            "what is my current assumption",
            "what's my current assumption",
            "what is our current assumption",
            "what's our current assumption",
            "what is our assumption",
            "what's our assumption",
            "what is the assumption",
            "what's the assumption",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_assumption",
            fact_name="profile_current_assumption",
            label="current assumption",
        )
    if any(
        phrase in text
        for phrase in (
            "what is my current owner",
            "what's my current owner",
            "what is our current owner",
            "what's our current owner",
            "what is our owner",
            "what's our owner",
            "who is the owner",
            "who owns this",
        )
    ):
        return _generic_profile_fact_query(
            predicate="profile.current_owner",
            fact_name="profile_current_owner",
            label="current owner",
        )
    if normalized_question in {
        "what startup did i create",
        "what company did i found",
        "which company did i found",
        "what did i found",
        "what have i founded",
        "what company have i founded",
        "what company did i start",
        "which company did i start",
    }:
        return ProfileFactQuery(
            predicate="profile.founder_of",
            fact_name="profile_founder_of",
            label="company you founded",
        )
    if normalized_question in {
        "what am i",
        "what is my occupation",
        "what's my occupation",
        "what do i do",
    }:
        return ProfileFactQuery(
            predicate="profile.occupation",
            fact_name="profile_occupation",
            label="occupation",
        )
    if any(
        phrase in text
        for phrase in (
            "what role will spark play in this",
            "what role will spark play",
            "how will spark help",
            "what will spark do in this",
        )
    ):
        return ProfileFactQuery(predicate="profile.spark_role", fact_name="profile_spark_role", label="spark role")
    if _is_identity_summary_query(text=text, normalized_question=normalized_question):
        return ProfileFactQuery(
            predicate=None,
            predicate_prefix="profile.",
            fact_name="profile_identity_summary",
            label="identity summary",
            query_kind="identity_summary",
        )
    if any(
        phrase in text
        for phrase in (
            "what country do you have for me",
            "what country do you have saved for me",
            "which country do you have for me",
            "what country do i live in",
            "which country do i live in",
            "what country am i in",
            "which country am i in",
            "what's my country",
            "what is my country",
        )
    ):
        return ProfileFactQuery(predicate="profile.home_country", fact_name="profile_home_country", label="country")
    if any(
        phrase in text
        for phrase in (
            "what timezone do you have for me",
            "what timezone do you have saved for me",
            "which timezone do you have for me",
            "what's my timezone",
            "what is my timezone",
        )
    ):
        return ProfileFactQuery(predicate="profile.timezone", fact_name="profile_timezone", label="timezone")
    if any(
        phrase in text
        for phrase in (
            "where do i live",
            "what city do i live in",
            "which city do i live in",
            "what city am i in",
            "which city am i in",
            "what city do you have for me",
            "what city do you have saved for me",
            "which city do you have for me",
        )
    ):
        return ProfileFactQuery(predicate="profile.city", fact_name="profile_city", label="city")
    return None


def _is_identity_summary_query(*, text: str, normalized_question: str) -> bool:
    if any(
        phrase in text
        for phrase in (
            "who am i",
            "what do you know about me",
            "what do you remember about me",
            "what do you remember for me",
            "what do you remember of me",
            "what do you have saved about me",
            "summarize my profile",
            "summarise my profile",
            "give me my profile summary",
            "give me a full profile summary",
            "give me the full profile summary",
        )
    ):
        return True
    if "profile summary" not in text:
        return False
    return any(
        phrase in normalized_question
        for phrase in (
            "my profile summary",
            "full profile summary",
            "latest location",
        )
    )


def build_profile_fact_query_context(*, query: ProfileFactQuery, value: str | None) -> str:
    label = query.label
    if value:
        concise_answer = build_profile_fact_query_answer(query=query, value=value)
        return (
            "[Memory action: PROFILE_FACT_STATUS]\n"
            f"The user is asking about their saved {label}. "
            f"Memory-backed current-state fact: {label}: {value}.\n"
            f"Expected concise answer: {concise_answer}\n"
            "Answer in one sentence only. Use the saved fact directly. "
            "Do not add broader narrative, strategy, backstory, or follow-up questions."
        )
    return (
        "[Memory action: PROFILE_FACT_STATUS_MISSING]\n"
        f"The user is asking about their saved {label}, but no memory-backed current-state fact is available.\n"
        "Answer in one sentence only. Do not pretend you know. "
        "Say you do not currently have that saved and invite the user to tell you if they want."
    )


def active_state_revalidation_days(predicate: str | None) -> int | None:
    normalized = str(predicate or "").strip().lower()
    return _ACTIVE_STATE_REVALIDATION_DAYS_BY_PREDICATE.get(normalized)


def active_state_revalidate_at(*, predicate: str | None, timestamp: str | None) -> str | None:
    revalidation_days = active_state_revalidation_days(predicate)
    if revalidation_days is None:
        return None
    normalized_timestamp = str(timestamp or "").strip()
    if not normalized_timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(normalized_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return (parsed + timedelta(days=revalidation_days)).isoformat()


def active_state_records_past_revalidation(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    stale_records: list[dict[str, Any]] = []
    for record in records:
        predicate = str(record.get("predicate") or "").strip()
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        revalidate_at = str(metadata.get("revalidate_at") or "").strip()
        if not revalidate_at:
            timestamp = str(record.get("timestamp") or record.get("document_time") or "").strip()
            revalidate_at = active_state_revalidate_at(predicate=predicate, timestamp=timestamp) or ""
        if not revalidate_at:
            continue
        try:
            parsed = datetime.fromisoformat(revalidate_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        if parsed <= now:
            stale_records.append(record)
    return stale_records


def _build_profile_fact_stale_answer(*, query: ProfileFactQuery, value: str) -> str:
    return (
        f'I have an older saved {query.label}: "{value}" '
        "but it has not been revalidated recently, so I would not treat it as current."
    )


def build_profile_fact_query_answer(*, query: ProfileFactQuery, value: str | None, stale: bool = False) -> str:
    normalized_value = str(value or "").strip()
    if not normalized_value:
        return "I don't currently have that saved."
    if stale:
        return _build_profile_fact_stale_answer(query=query, value=normalized_value)
    return _build_profile_fact_concise_answer(query=query, value=normalized_value)


def build_profile_fact_explanation_answer(*, query: ProfileFactQuery, explanation: dict[str, object] | None) -> str:
    payload = explanation or {}
    answer_value = str(payload.get("answer") or "").strip() or None
    if not answer_value and not payload.get("evidence") and not payload.get("events"):
        return "I don't currently have a supported memory explanation for that."
    concise_answer = build_profile_fact_query_answer(query=query, value=answer_value)
    evidence = payload.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, dict):
                continue
            evidence_text = str(item.get("text") or "").strip()
            if evidence_text:
                return f'Because I have a saved memory record from when you said: "{evidence_text}" {concise_answer}'
    return f"Because I have a saved memory record for that. {concise_answer}"


def build_profile_fact_history_answer(
    *,
    query: ProfileFactQuery,
    previous_value: str | None,
    current_value: str | None = None,
) -> str:
    normalized_previous = str(previous_value or "").strip()
    normalized_current = str(current_value or "").strip()
    if not normalized_previous:
        return f"I don't currently have an earlier saved {query.label}."
    predicate = str(query.predicate or "").strip()
    if predicate == "profile.city":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(f"Before {normalized_current}, you lived in {normalized_previous}")
        return _ensure_sentence(f"An earlier saved city was {normalized_previous}")
    if predicate == "profile.home_country":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(f"Before {normalized_current}, your country was {normalized_previous}")
        return _ensure_sentence(f"An earlier saved country was {normalized_previous}")
    if predicate == "profile.timezone":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(f"Before {normalized_current}, your timezone was {normalized_previous}")
        return _ensure_sentence(f"An earlier saved timezone was {normalized_previous}")
    if predicate == "profile.current_plan":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(f"Before your current plan was to {normalized_current}, it was to {normalized_previous}")
        return _ensure_sentence(f"An earlier saved current plan was to {normalized_previous}")
    if predicate == "profile.current_focus":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(f"Before your current focus was {normalized_current}, it was {normalized_previous}")
        return _ensure_sentence(f"An earlier saved current focus was {normalized_previous}")
    if predicate == "profile.current_decision":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(
                f"Before your current decision was {normalized_current}, it was {normalized_previous}"
            )
        return _ensure_sentence(f"An earlier saved current decision was {normalized_previous}")
    if predicate == "profile.current_blocker":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(
                f"Before your current blocker was {normalized_current}, it was {normalized_previous}"
            )
        return _ensure_sentence(f"An earlier saved current blocker was {normalized_previous}")
    if predicate == "profile.current_status":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(
                f"Before your current status was {normalized_current}, it was {normalized_previous}"
            )
        return _ensure_sentence(f"An earlier saved current status was {normalized_previous}")
    if predicate == "profile.current_commitment":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(
                f"Before your current commitment was to {normalized_current}, it was to {normalized_previous}"
            )
        return _ensure_sentence(f"An earlier saved current commitment was to {normalized_previous}")
    if predicate == "profile.current_milestone":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(
                f"Before your current milestone was {normalized_current}, it was {normalized_previous}"
            )
        return _ensure_sentence(f"An earlier saved current milestone was {normalized_previous}")
    if predicate == "profile.current_risk":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(f"Before your current risk was {normalized_current}, it was {normalized_previous}")
        return _ensure_sentence(f"An earlier saved current risk was {normalized_previous}")
    if predicate == "profile.current_dependency":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(
                f"Before your current dependency was {normalized_current}, it was {normalized_previous}"
            )
        return _ensure_sentence(f"An earlier saved current dependency was {normalized_previous}")
    if predicate == "profile.current_constraint":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(
                f"Before your current constraint was {normalized_current}, it was {normalized_previous}"
            )
        return _ensure_sentence(f"An earlier saved current constraint was {normalized_previous}")
    if predicate == "profile.current_assumption":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(
                f"Before your current assumption was {normalized_current}, it was {normalized_previous}"
            )
        return _ensure_sentence(f"An earlier saved current assumption was {normalized_previous}")
    if predicate == "profile.current_owner":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(f"Before your current owner was {normalized_current}, it was {normalized_previous}")
        return _ensure_sentence(f"An earlier saved current owner was {normalized_previous}")
    if predicate == "profile.cofounder_name":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(f"Before {normalized_current}, your cofounder was {normalized_previous}")
        return _ensure_sentence(f"An earlier saved cofounder was {normalized_previous}")
    if predicate == "profile.mentor_name":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(f"Before {normalized_current}, your mentor was {normalized_previous}")
        return _ensure_sentence(f"An earlier saved mentor was {normalized_previous}")
    if predicate == "profile.manager_name":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(f"Before {normalized_current}, your manager was {normalized_previous}")
        return _ensure_sentence(f"An earlier saved manager was {normalized_previous}")
    if predicate == "profile.assistant_name":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(f"Before {normalized_current}, your assistant was {normalized_previous}")
        return _ensure_sentence(f"An earlier saved assistant was {normalized_previous}")
    if predicate == "profile.partner_name":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(f"Before {normalized_current}, your partner was {normalized_previous}")
        return _ensure_sentence(f"An earlier saved partner was {normalized_previous}")
    if predicate == "profile.mother_name":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(f"Before {normalized_current}, your mother was {normalized_previous}")
        return _ensure_sentence(f"An earlier saved mother was {normalized_previous}")
    if predicate == "profile.father_name":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(f"Before {normalized_current}, your father was {normalized_previous}")
        return _ensure_sentence(f"An earlier saved father was {normalized_previous}")
    if predicate == "profile.sister_name":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(f"Before {normalized_current}, your sister was {normalized_previous}")
        return _ensure_sentence(f"An earlier saved sister was {normalized_previous}")
    if predicate == "profile.brother_name":
        if normalized_current and normalized_current != normalized_previous:
            return _ensure_sentence(f"Before {normalized_current}, your brother was {normalized_previous}")
        return _ensure_sentence(f"An earlier saved brother was {normalized_previous}")
    return _ensure_sentence(f"An earlier saved {query.label} was {normalized_previous}")


def build_profile_fact_event_history_answer(*, query: ProfileFactQuery, records: list[dict[str, object]]) -> str:
    values: list[str] = []
    last_value = ""
    for record in records:
        value = str(record.get("value") or record.get("normalized_value") or "").strip()
        if not value or value == last_value:
            continue
        values.append(value)
        last_value = value
    if not values:
        return f"I don't currently have saved {query.label} events."
    if len(values) == 1:
        return _ensure_sentence(f"I only have one saved {query.label} event: {values[0]}")
    return _ensure_sentence(f"I have {len(values)} saved {query.label} events: {' then '.join(values)}")


def build_profile_fact_observation_answer(*, observation: ProfileFactObservation) -> str:
    predicate = str(observation.predicate or "").strip()
    value = str(observation.value or "").strip()
    if not predicate or not value:
        return "I'll remember that."
    if predicate == "profile.preferred_name":
        return _ensure_sentence(f"I'll remember your name is {value}")
    if predicate == "profile.startup_name":
        return _ensure_sentence(f"I'll remember you created {value}")
    if predicate == "profile.occupation":
        return _ensure_sentence(f"I'll remember you're {_with_indefinite_article(value)}")
    if predicate == "profile.founder_of":
        return _ensure_sentence(f"I'll remember you founded {value}")
    if predicate == "profile.hack_actor":
        return _ensure_sentence(f"I'll remember the hack actor was {value}")
    if predicate == "profile.current_mission":
        return _ensure_sentence(f"I'll remember your current mission is to {value}")
    if predicate == "profile.spark_role":
        return _ensure_sentence(f"I'll remember {_spark_role_sentence(value)}")
    if predicate == "profile.home_country":
        return _ensure_sentence(f"I'll remember your country is {value}")
    if predicate == "profile.timezone":
        return _ensure_sentence(f"I'll remember your timezone is {value}")
    if predicate == "profile.city":
        return _ensure_sentence(f"I'll remember you live in {value}")
    return _ensure_sentence(f"I'll remember your {observation.fact_name.replace('profile_', '').replace('_', ' ')} is {value}")


def _build_profile_fact_concise_answer(*, query: ProfileFactQuery, value: str) -> str:
    normalized_value = str(value or "").strip()
    if not normalized_value:
        return "I do not currently have that saved."

    predicate = str(query.predicate or "").strip()
    if predicate == "profile.preferred_name":
        return _ensure_sentence(f"Your name is {normalized_value}")
    if predicate == "profile.occupation":
        return _ensure_sentence(f"You're {_with_indefinite_article(normalized_value)}")
    if predicate == "profile.startup_name":
        return _ensure_sentence(f"You created {normalized_value}")
    if predicate == "profile.founder_of":
        return _ensure_sentence(f"You founded {normalized_value}")
    if predicate == "profile.hack_actor":
        return _ensure_sentence(f"The hack actor was {normalized_value}")
    if predicate == "profile.current_mission":
        return _ensure_sentence(f"Right now you're trying to {normalized_value}")
    if predicate == "profile.current_plan":
        return _ensure_sentence(f"Your current plan is to {normalized_value}")
    if predicate == "profile.current_focus":
        return _ensure_sentence(f"Your current focus is {normalized_value}")
    if predicate == "profile.current_decision":
        return _ensure_sentence(f"Your current decision is {normalized_value}")
    if predicate == "profile.current_blocker":
        return _ensure_sentence(f"Your current blocker is {normalized_value}")
    if predicate == "profile.current_status":
        return _ensure_sentence(f"Your current status is {normalized_value}")
    if predicate == "profile.current_commitment":
        return _ensure_sentence(f"Your current commitment is to {normalized_value}")
    if predicate == "profile.current_milestone":
        return _ensure_sentence(f"Your current milestone is {normalized_value}")
    if predicate == "profile.current_risk":
        return _ensure_sentence(f"Your current risk is {normalized_value}")
    if predicate == "profile.current_dependency":
        return _ensure_sentence(f"Your current dependency is {normalized_value}")
    if predicate == "profile.current_constraint":
        return _ensure_sentence(f"Your current constraint is {normalized_value}")
    if predicate == "profile.current_assumption":
        return _ensure_sentence(f"Your current assumption is {normalized_value}")
    if predicate == "profile.current_owner":
        return _ensure_sentence(f"Your current owner is {normalized_value}")
    if predicate == "profile.cofounder_name":
        return _ensure_sentence(f"Your cofounder is {normalized_value}")
    if predicate == "profile.mentor_name":
        return _ensure_sentence(f"Your mentor is {normalized_value}")
    if predicate == "profile.manager_name":
        return _ensure_sentence(f"Your manager is {normalized_value}")
    if predicate == "profile.assistant_name":
        return _ensure_sentence(f"Your assistant is {normalized_value}")
    if predicate == "profile.partner_name":
        return _ensure_sentence(f"Your partner is {normalized_value}")
    if predicate == "profile.mother_name":
        return _ensure_sentence(f"Your mother is {normalized_value}")
    if predicate == "profile.father_name":
        return _ensure_sentence(f"Your father is {normalized_value}")
    if predicate == "profile.sister_name":
        return _ensure_sentence(f"Your sister is {normalized_value}")
    if predicate == "profile.brother_name":
        return _ensure_sentence(f"Your brother is {normalized_value}")
    if predicate == "profile.spark_role":
        return _ensure_sentence(_spark_role_sentence(normalized_value))
    if predicate == "profile.home_country":
        return _ensure_sentence(f"Your country is {normalized_value}")
    if predicate == "profile.timezone":
        return _ensure_sentence(f"Your timezone is {normalized_value}")
    if predicate == "profile.city":
        return _ensure_sentence(f"You live in {normalized_value}")
    return _ensure_sentence(f"Your saved {query.label} is {normalized_value}")


def _ensure_sentence(text: str) -> str:
    normalized = " ".join(str(text or "").strip().split())
    if not normalized:
        return ""
    if normalized[-1] in ".!?":
        return normalized
    return f"{normalized}."


def _spark_role_sentence(value: str) -> str:
    normalized = " ".join(str(value or "").strip().split())
    if normalized.lower().startswith("important part"):
        return f"Spark will be an {normalized}"
    return f"Spark will be {normalized}"


def _with_indefinite_article(value: str) -> str:
    normalized = " ".join(str(value or "").strip().split())
    if not normalized:
        return ""
    lowered = normalized.lower()
    if lowered.startswith("a ") or lowered.startswith("an "):
        return normalized
    article = "an" if normalized[:1].lower() in {"a", "e", "i", "o", "u"} else "a"
    return f"{article} {normalized}"


def build_profile_identity_summary_context(*, records: list[dict[str, str]]) -> str:
    preferred_order = [
        "profile.preferred_name",
        "profile.occupation",
        "profile.startup_name",
        "profile.founder_of",
        "profile.hack_actor",
        "profile.current_mission",
        "profile.current_plan",
        "profile.current_focus",
        "profile.current_decision",
        "profile.current_blocker",
        "profile.current_status",
        "profile.current_commitment",
        "profile.current_milestone",
        "profile.current_risk",
        "profile.current_dependency",
        "profile.current_constraint",
        "profile.current_assumption",
        "profile.current_owner",
        "profile.cofounder_name",
        "profile.mentor_name",
        "profile.manager_name",
        "profile.assistant_name",
        "profile.partner_name",
        "profile.mother_name",
        "profile.father_name",
        "profile.sister_name",
        "profile.brother_name",
        "profile.spark_role",
        "profile.city",
        "profile.home_country",
        "profile.timezone",
    ]
    label_map = {
        "profile.preferred_name": "name",
        "profile.occupation": "occupation",
        "profile.startup_name": "startup",
        "profile.founder_of": "founder of",
        "profile.hack_actor": "hacked by",
        "profile.current_mission": "current mission",
        "profile.current_plan": "current plan",
        "profile.current_focus": "current focus",
        "profile.current_decision": "current decision",
        "profile.current_blocker": "current blocker",
        "profile.current_status": "current status",
        "profile.current_commitment": "current commitment",
        "profile.current_milestone": "current milestone",
        "profile.current_risk": "current risk",
        "profile.current_dependency": "current dependency",
        "profile.current_constraint": "current constraint",
        "profile.current_assumption": "current assumption",
        "profile.current_owner": "current owner",
        "profile.cofounder_name": "cofounder",
        "profile.mentor_name": "mentor",
        "profile.manager_name": "manager",
        "profile.assistant_name": "assistant",
        "profile.partner_name": "partner",
        "profile.mother_name": "mother",
        "profile.father_name": "father",
        "profile.sister_name": "sister",
        "profile.brother_name": "brother",
        "profile.spark_role": "Spark role",
        "profile.city": "city",
        "profile.home_country": "country",
        "profile.timezone": "timezone",
    }
    value_by_predicate: dict[str, str] = {}
    for record in records:
        predicate = str(record.get("predicate") or "").strip()
        value = str(record.get("value") or "").strip()
        if predicate and value:
            value_by_predicate[predicate] = value
    lines = [
        "[Memory action: PROFILE_IDENTITY_SUMMARY]",
        "The user is asking who they are or what you remember about them.",
    ]
    summary_rows = [
        f"- {label_map[predicate]}: {value_by_predicate[predicate]}"
        for predicate in preferred_order
        if predicate in value_by_predicate
    ]
    if summary_rows:
        lines.append("Memory-backed facts:")
        lines.extend(summary_rows)
        lines.append("Answer naturally using only these saved facts and do not invent anything beyond them.")
        return "\n".join(lines)
    lines.append("No memory-backed identity facts are available.")
    lines.append("Do not pretend you know. Say you do not currently have that saved and invite the user to tell you.")
    return "\n".join(lines)


def build_profile_identity_summary_answer(*, records: list[dict[str, str]]) -> str:
    value_by_predicate: dict[str, str] = {}
    for record in records:
        predicate = str(record.get("predicate") or "").strip()
        value = str(record.get("value") or "").strip()
        if predicate and value:
            value_by_predicate[predicate] = value

    if not value_by_predicate:
        return "I don't currently have identity details saved for you."

    sentences: list[str] = []
    name = value_by_predicate.get("profile.preferred_name")
    occupation = value_by_predicate.get("profile.occupation")
    city = value_by_predicate.get("profile.city")

    identity_bits: list[str] = []
    if name:
        identity_bits.append(name)
    if occupation:
        identity_bits.append(_with_indefinite_article(occupation))
    if city:
        identity_bits.append(f"in {city}")
    if identity_bits:
        if name and occupation and city:
            sentences.append(_ensure_sentence(f"You're {name}, {' '.join(identity_bits[1:])}"))
        elif name:
            trailing = " ".join(identity_bits[1:]).strip()
            if trailing:
                sentences.append(_ensure_sentence(f"You're {name}, {trailing}"))
            else:
                sentences.append(_ensure_sentence(f"You're {name}"))
        else:
            sentences.append(_ensure_sentence(f"You're {' '.join(identity_bits)}"))

    startup_name = value_by_predicate.get("profile.startup_name")
    founder_of = value_by_predicate.get("profile.founder_of")
    if founder_of:
        sentences.append(_ensure_sentence(f"You founded {founder_of}"))
    elif startup_name:
        sentences.append(_ensure_sentence(f"Your startup is {startup_name}"))
    if startup_name and founder_of and startup_name != founder_of:
        sentences.append(_ensure_sentence(f"Your startup is {startup_name}"))

    hack_actor = value_by_predicate.get("profile.hack_actor")
    if hack_actor:
        sentences.append(_ensure_sentence(f"{hack_actor} hacked you"))

    current_mission = value_by_predicate.get("profile.current_mission")
    if current_mission:
        sentences.append(_ensure_sentence(f"Your current mission is to {current_mission}"))

    spark_role = value_by_predicate.get("profile.spark_role")
    if spark_role:
        sentences.append(_ensure_sentence(_spark_role_sentence(spark_role)))

    home_country = value_by_predicate.get("profile.home_country")
    if home_country:
        if city:
            sentences.append(_ensure_sentence(f"Your country is {home_country}"))
        else:
            sentences.append(_ensure_sentence(f"You're based in {home_country}"))

    timezone = value_by_predicate.get("profile.timezone")
    if timezone:
        sentences.append(_ensure_sentence(f"Your timezone is {timezone}"))

    if not sentences:
        first_predicate = next(iter(value_by_predicate))
        first_value = value_by_predicate[first_predicate]
        sentences.append(_ensure_sentence(f"Your saved identity detail is {first_value}"))
    return " ".join(sentences)


def _extract_city(text: str) -> str | None:
    for pattern in _CITY_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = _normalize_city(match.group(1))
        if candidate:
            return candidate
    return None


def _extract_startup(text: str) -> str | None:
    for pattern in _STARTUP_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = _normalize_entity_name(match.group(1))
        if candidate:
            return candidate
    return None


def _extract_founder_of(text: str) -> str | None:
    for pattern in _FOUNDER_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = _normalize_entity_name(match.group(1))
        if candidate:
            return candidate
    return None


def _extract_hack_actor(text: str) -> str | None:
    for pattern in _HACK_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = _normalize_place(match.group(1))
        if candidate:
            return candidate
    return None


def _extract_current_mission(text: str) -> str | None:
    for pattern in _MISSION_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = re.split(r"[.!?]", str(match.group(1) or ""), maxsplit=1)[0].strip(" '\"`")
        candidate = re.sub(r"\b(?:now|today|currently)\b\s*$", "", candidate, flags=re.I).strip(" '\"`")
        if candidate:
            return candidate.lower()
    return None


def _extract_spark_role(text: str) -> str | None:
    for pattern in _SPARK_ROLE_PATTERNS:
        if pattern.search(text):
            return "important part of the rebuild"
    return None


def _extract_occupation(text: str) -> str | None:
    for pattern in _OCCUPATION_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = re.sub(
            r"\b(?:now|today|currently)\b\s*$",
            "",
            str(match.group(1) or "").strip(),
            flags=re.I,
        ).strip().lower()
        if candidate:
            return candidate
    return None


def _extract_name(text: str) -> str | None:
    for pattern in _NAME_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = _normalize_name(match.group(1))
        if candidate:
            return candidate
    return None


def _extract_country(text: str) -> str | None:
    for pattern in _COUNTRY_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = _normalize_country_name(match.group(1)) or _normalize_place(match.group(1))
        if candidate:
            return candidate
    for pattern in _COUNTRY_IN_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = _normalize_country_name(match.group(1))
        if candidate:
            return candidate
    for pattern in _COUNTRY_LIVE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = _normalize_country_name(match.group(1))
        if candidate:
            return candidate
    for pattern in _COUNTRY_MOVE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = _normalize_country_name(match.group(1))
        if candidate:
            return candidate
    return None


def _extract_timezone(text: str) -> str | None:
    for pattern in _TIMEZONE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = _normalize_timezone(match.group(1))
        if candidate:
            return candidate
    return None


def _normalize_city(raw: str) -> str | None:
    return _normalize_place(raw)


def _normalize_name(raw: str) -> str | None:
    candidate = re.split(r"[.!?,;:\n]", str(raw or ""), maxsplit=1)[0].strip(" '\"`")
    if not candidate:
        return None
    parts = []
    for token in candidate.split():
        lowered = token.lower()
        if lowered in _STOP_WORDS or lowered in _TEMPORAL_TAIL_WORDS:
            break
        cleaned = re.sub(r"[^A-Za-z'\-]", "", token)
        if not cleaned:
            continue
        parts.append(cleaned)
        if len(parts) >= 3:
            break
    if not parts:
        return None
    normalized = []
    for token in parts:
        if token.isupper() and len(token) <= 4:
            normalized.append(token)
        else:
            normalized.append(token[0].upper() + token[1:].lower())
    return " ".join(normalized)


def _normalize_place(raw: str) -> str | None:
    candidate = re.split(r"[.!?,;:\n]", str(raw or ""), maxsplit=1)[0].strip(" '\"`")
    if not candidate:
        return None
    parts = []
    for token in candidate.split():
        lowered = token.lower()
        if lowered in _STOP_WORDS or lowered in _TEMPORAL_TAIL_WORDS:
            break
        cleaned = re.sub(r"[^A-Za-z'\-]", "", token)
        if not cleaned:
            continue
        parts.append(cleaned)
        if len(parts) >= 4:
            break
    if not parts:
        return None
    normalized: list[str] = []
    for index, token in enumerate(parts):
        lowered = token.lower()
        if index > 0 and lowered in _LOWERCASE_JOINERS:
            normalized.append(lowered)
        elif token.isupper() and len(token) <= 4:
            normalized.append(token)
        else:
            normalized.append(lowered[0].upper() + lowered[1:])
    return " ".join(normalized)


def _normalize_country_name(raw: str) -> str | None:
    candidate = _normalize_place(raw)
    if not candidate:
        return None
    lowered = candidate.lower()
    normalized = _KNOWN_COUNTRY_NAMES.get(lowered)
    if normalized:
        return normalized
    if lowered.startswith("the "):
        return _KNOWN_COUNTRY_NAMES.get(lowered[4:].strip())
    return None


def _normalize_entity_name(raw: str) -> str | None:
    candidate = re.split(r"[.!?,;:\n]", str(raw or ""), maxsplit=1)[0].strip(" '\"`")
    if not candidate:
        return None
    parts = []
    for token in candidate.split():
        lowered = token.lower()
        if lowered in _STOP_WORDS or lowered in _TEMPORAL_TAIL_WORDS:
            break
        cleaned = re.sub(r"[^A-Za-z0-9'\-&_.]", "", token)
        if not cleaned:
            continue
        parts.append(cleaned)
        if len(parts) >= 6:
            break
    if not parts:
        return None
    normalized = []
    for token in parts:
        if token.isupper() and len(token) <= 5:
            normalized.append(token)
        elif any(char.isupper() for char in token[1:]):
            normalized.append(token)
        else:
            normalized.append(token[0].upper() + token[1:])
    return " ".join(normalized)


def _normalize_timezone(raw: str) -> str | None:
    candidate = re.split(r"[.!?,;:\n]", str(raw or ""), maxsplit=1)[0].strip(" '\"`")
    if not candidate:
        return None
    if "/" in candidate:
        parts = [part for part in candidate.split("/") if part]
        if len(parts) < 2:
            return None
        normalized = []
        for part in parts[:3]:
            cleaned = re.sub(r"[^A-Za-z_]", "", part)
            if not cleaned:
                return None
            normalized.append("_".join(token.capitalize() for token in cleaned.split("_") if token))
        return "/".join(normalized)
    compact = candidate.replace(" ", "").upper()
    if re.fullmatch(r"UTC[+-]\d{1,2}(?::\d{2})?", compact):
        return compact
    return None
