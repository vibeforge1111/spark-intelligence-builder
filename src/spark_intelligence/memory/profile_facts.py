from __future__ import annotations

import re
from dataclasses import dataclass


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
    re.compile(r"\bspark\s+(?:is\s+going\s+to\s+be|will\s+be)\s+an\s+important\s+part\s+of\s+this(?:\s+rebuild)?", re.I),
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


def detect_profile_fact_observation(user_message: str) -> ProfileFactObservation | None:
    text = str(user_message or "").strip()
    if not text:
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
            "what startup did i create",
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
    if normalized_question in {
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
    if any(
        phrase in text
        for phrase in (
            "who am i",
            "what do you know about me",
            "what do you have saved about me",
        )
    ):
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


def build_profile_fact_query_answer(*, query: ProfileFactQuery, value: str | None) -> str:
    normalized_value = str(value or "").strip()
    if not normalized_value:
        return "I don't currently have that saved."
    return _build_profile_fact_concise_answer(query=query, value=normalized_value)


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
        "profile.spark_role": "Spark role",
        "profile.city": "city",
        "profile.home_country": "country",
        "profile.timezone": "timezone",
    }
    value_by_predicate: dict[str, str] = {}
    for record in records:
        predicate = str(record.get("predicate") or "").strip()
        value = str(record.get("value") or "").strip()
        if predicate and value and predicate not in value_by_predicate:
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
        if predicate and value and predicate not in value_by_predicate:
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

    founded_values: list[str] = []
    startup_name = value_by_predicate.get("profile.startup_name")
    founder_of = value_by_predicate.get("profile.founder_of")
    for candidate in (startup_name, founder_of):
        if candidate and candidate not in founded_values:
            founded_values.append(candidate)
    if founded_values:
        if len(founded_values) == 1:
            sentences.append(_ensure_sentence(f"You founded {founded_values[0]}"))
        elif len(founded_values) == 2:
            sentences.append(_ensure_sentence(f"You founded {founded_values[0]} and {founded_values[1]}"))
        else:
            sentences.append(_ensure_sentence(f"You founded {', '.join(founded_values[:-1])}, and {founded_values[-1]}"))

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
    if home_country and not city:
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
        candidate = _normalize_place(match.group(1))
        if candidate:
            return candidate
    for pattern in _COUNTRY_IN_PATTERNS:
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
    return _KNOWN_COUNTRY_NAMES.get(candidate.lower())


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
