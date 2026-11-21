"""Phrase-level B-roll concepts from the transcript (Smart B-Roll v3 cue source).

Why this exists: the keyword path scanned ~25 single nouns and fired each one
at most once per clip, so a talking clip rarely produced more than one usable
cue (measured: 0.6–1.7 cues per 40 s clip on real speech, Phase 0 audit). The
LLM concept writer (``clip_plan`` broll_concepts) remains the primary source
whenever a model plan exists; this module is the keyless source and the
top-up when the plan wrote fewer concepts than the clip's cadence allows.

What makes a cue here more than a keyword match:

* **Phrases first** — "stock market", "interest rates", "credit card debt"
  outrank single words, and every stock search phrase has >= 2 words (a bare
  noun is read by a stock library in its most common sense only).
* **Sense context** — a trap word ("market", "bank", "interest", "train"…)
  only fires with the context words that fix its meaning nearby, and the cue
  carries the resolved ``sense`` tag the rest of the pipeline already uses.
* **Topic coherence** — a candidate scores higher when other words from its
  domain are spoken around it (a "chart" inside a finance stretch, not a
  passing mention), and higher again when the domain fits the niche.
* **Avoid lists** — sensitive topics never search for gore, weapons or
  nudity; the SigLIP veto still judges every downloaded frame at resolve time.

Output: concept dicts in exactly the shape ``edl._concept_broll_queries``
accepts (anchor_sec, spoken, concept, search_queries, sense, media, avoid,
duration_sec) plus ``cue_source: "lexicon"``. Pure and deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# --------------------------------------------------------------------------
# The lexicon: id | domain | triggers | stock queries | options
#   triggers: "|"-separated lowercase phrases (last word may be plural)
#   queries:  ";"-separated stock search phrases, each >= 2 words
#   options:  sense=<trap>:<sense>  ctx=<word,word>  generic  photo  avoid=<a,b>  dur=<s>
# --------------------------------------------------------------------------
_RAW = """
stock_market | finance | stock market|stocks|wall street|share price|nasdaq|dow jones|s&p 500|index fund|etf | stock market trading screen; stock chart on monitor; financial district skyscrapers | dur=3.5
market_fin | finance | market | stock market chart screen; trading floor screens | sense=market:financial ctx=stock,stocks,shares,invest,investing,investor,price,prices,trading,trade,trades,strategy,odds,probabilities,profit,loss,winners,losers,economy,crash,bull,bear,inflation,money,fund,funds,portfolio
trading | finance | trade|trades|trader|traders|trading|day trading|day trader|trading strategy | trader looking at stock charts; day trading multiple monitors; stock chart candlesticks | ctx=profit,loss,losses,win,winning,winners,losers,market,strategy,odds,stock,stocks,chart,charts,money,account,position,risk,probabilities amb=trade,trades
market_crash | finance | market crash|crash|recession|downturn|sell off|selloff | falling stock chart red; stock market crash screen | ctx=market,stock,stocks,economy,shares,prices,investors amb=crash
bull_market | finance | bull market|bear market | stock chart going up; green stock market screen | sense=bull:financial
investing | finance | investing|investment|investor|portfolio|returns|compound interest|dividends|mutual fund | investment portfolio on laptop; person checking investments on phone
interest_rate | finance | interest rate|interest rates|federal reserve|rbi|central bank|rate hike | central bank building exterior; interest rate chart on screen | sense=interest:financial
bank_fin | finance | bank|banks|banking|bank account|savings account | bank building exterior; atm cash withdrawal | sense=bank:financial ctx=money,account,loan,loans,deposit,savings,credit,interest,cash,pay,paid,salary,transfer,finance,financial
loan_debt | finance | loan|loans|debt|mortgage|emi|borrow|borrowing|credit score | signing loan documents; person stressed over bills
credit_card | finance | credit card|credit cards|debit card|swipe | paying with credit card; credit card close up in hand; card payment at shop counter | avoid=airport,escalator,walkway,train
cash_money | money | money|cash|dollars|rupees|bucks|currency | counting cash money; stack of banknotes | generic avoid=casino,gambling
salary | money | salary|paycheck|income|earn|earning|earnings|wages | person receiving salary payment; payday cash in hand | generic
budget | money | budget|budgeting|expenses|spending|savings|save money|saving money | budget planning notebook calculator; household budget spreadsheet
tax | money | tax|taxes|irs|gst|income tax|tax return | tax forms and calculator; filling tax documents
inflation | finance | inflation|prices|price rise|cost of living|expensive|groceries prices | grocery store prices shelf; rising prices supermarket | ctx=money,price,prices,cost,expensive,economy,rupees,dollars,inflation,buy amb=prices,expensive
crypto | finance | crypto|cryptocurrency|bitcoin|ethereum|blockchain|nft|nfts | bitcoin cryptocurrency coin; crypto trading chart | proper
gold_invest | finance | gold price|gold prices|gold investment|buying gold | gold bars close up; gold jewelry shop
real_estate | real_estate | real estate|property|properties|house prices|rent|landlord|apartment|home loan | modern apartment building exterior; real estate house for sale
startup | business | startup|startups|founder|founders|entrepreneur|entrepreneurs|venture capital|funding round|unicorn | startup team brainstorming office; entrepreneur working on laptop
company_office | business | company|companies|corporation|business|businesses|firm | corporate office building; business people walking office | generic
meeting | business | meeting|meetings|presentation|pitch deck|boardroom|conference | business meeting conference room; team presentation boardroom
ceo_boss | business | ceo|boss|manager|executive|leadership|leader | business executive in office; ceo walking in office
sales_marketing | business | marketing|advertising|ads|brand|branding|sales|customers|customer | marketing team planning; advertising billboard city | ctx=marketing,advertising,campaign,customers,customer,sales,brand,brands,audience,business,company,promote,promotion amb=ads,brand,branding,sales
ecommerce | business | online shopping|ecommerce|e-commerce|amazon|delivery|order online|shipping | online shopping on laptop; package delivery doorstep | proper
factory | business | factory|manufacturing|production line|warehouse|supply chain|logistics | factory production line; warehouse logistics workers
office_work | work | office|desk|nine to five|9 to 5|corporate job|job|career|employee|employees | office workers at desks; person typing at office desk | generic nprev=steve
remote_work | work | work from home|remote work|working from home|freelance|freelancer|freelancing | person working from home laptop; freelancer coffee shop laptop
hiring | work | interview|hiring|recruiter|resume|cv|job interview|fired|layoffs|unemployment | job interview handshake; people waiting job interview | ctx=job,jobs,hiring,hire,hired,resume,cv,recruiter,candidate,candidates,salary,offer,position,role,apply,applying,hr,employer amb=interview,fired
team | work | team|teamwork|colleagues|coworkers|collaboration | team collaboration office; colleagues high five | generic
productivity | work | productivity|productive|deadline|deadlines|to do list|schedule|calendar | planner calendar writing; person checking to do list
ai_tech | ai | artificial intelligence|ai|chatgpt|chat gpt|machine learning|neural network|llm|openai|claude|gemini|robot|robots | artificial intelligence digital brain; robot arm technology | dur=3.5 proper
coding | tech | code|coding|programming|programmer|developer|developers|software|app|apps|website|algorithm | programmer typing code on laptop; code on computer screen
computer | tech | computer|computers|laptop|pc|keyboard|monitor | person typing on laptop keyboard; computer setup desk
smartphone | tech | phone|phones|smartphone|iphone|android|mobile|screen time | person scrolling smartphone; hand holding smartphone screen
internet | tech | internet|online|wifi|data|cloud|server|servers|data center | data center server racks; internet network connection | generic
cyber | tech | hacker|hackers|hacking|cyber attack|password|passwords|scam|scams|fraud|phishing | hacker typing dark room; cyber security lock screen
social_media | social | social media|instagram|tiktok|youtube|followers|likes|views|influencer|influencers|content creator|creators|reels|shorts | scrolling social media feed phone; content creator filming video | proper
camera_filming | social | camera|filming|vlog|recording|podcast|microphone|studio | content creator recording camera; podcast microphone studio
gaming_setup | gaming | video game|video games|gaming|gamer|gamers|esports|console|playstation|xbox|controller | gamer playing video games; gaming controller close up | proper
electric_car | car | electric car|electric cars|ev|evs|tesla|charging station | electric car charging station; electric vehicle driving | proper
car_driving | car | car|cars|driving|driver|road trip|highway|traffic | car driving on highway; city traffic at night | generic
traffic_jam | city | traffic jam|traffic|rush hour|commute|commuting | city traffic jam; rush hour commuters | sense=jam:traffic
city | city | city|cities|downtown|skyline|skyscraper|skyscrapers|new york|london|tokyo|mumbai|delhi|bangalore|dubai | city skyline aerial; busy city street people | proper
train_rail | transport | train station|trains|railway|metro|subway|bullet train | train arriving at station; subway metro train | sense=train:railway
flight | transport | flight|flights|airport|plane|airplane|airline|airlines|flying | airplane taking off runway; airport terminal travelers
travel | travel | travel|traveling|travelling|trip|vacation|holiday|tourist|tourists|abroad|passport | traveler with backpack; tourist walking old town
beach | nature | beach|beaches|ocean|sea|island|islands|coast | tropical beach waves; ocean waves aerial
mountain | nature | mountain|mountains|hiking|hike|trek|trekking|himalayas|alps | mountain landscape aerial; hikers on mountain trail
forest | nature | forest|forests|trees|jungle|woods|nature | forest aerial view; sunlight through forest trees
river_lake | nature | river|rivers|lake|lakes|waterfall|waterfalls | river flowing forest; waterfall in nature
sunset | nature | sunset|sunrise|golden hour|dawn|dusk | sunset sky timelapse; sunrise over landscape
weather_rain | weather | rain|raining|storm|storms|thunder|lightning|monsoon | rain falling window; storm lightning sky
weather_heat | weather | heatwave|heat wave|hot weather|summer heat|drought | sun heat haze; dry cracked earth drought
snow_cold | weather | snow|snowing|winter|cold weather|freezing | snow falling winter; snowy landscape
climate | environment | climate change|global warming|carbon|emissions|pollution|plastic|recycling|sustainability | factory smoke pollution; recycling plastic bottles
energy | energy | solar|solar panels|wind turbines|renewable|electricity|power grid|oil|petrol|gas prices|fuel | solar panels field; wind turbines landscape
space | science | space|nasa|isro|rocket|rockets|mars|moon landing|astronaut|astronauts|galaxy|planet|planets|universe|satellite | rocket launch; earth from space | proper
science_lab | science | science|scientist|scientists|research|researchers|study|studies|experiment|lab|laboratory | scientist in laboratory; microscope research lab
doctor_health | health | doctor|doctors|hospital|hospitals|clinic|patients|nurse|healthcare|medicine|medical | doctor talking to patient; hospital corridor | avoid=blood,surgery,injury
mental_health | health | mental health|anxiety|depression|stress|stressed|burnout|therapy|therapist|overthinking | stressed person head in hands; therapy session calm
sleep | health | sleep|sleeping|insomnia|bed|bedtime|tired|fatigue | person sleeping in bed; alarm clock morning
fitness | fitness | gym|workout|workouts|exercise|exercising|lifting|fitness|muscle|muscles|bodybuilding | gym workout weights; person exercising gym
running | fitness | running|runner|jogging|marathon|cardio | runner jogging park; marathon runners street
diet | health | diet|dieting|calories|protein|weight loss|lose weight|fat loss|healthy food|nutrition | healthy food salad bowl; weighing scale feet
yoga_meditation | health | yoga|meditation|meditate|mindfulness|breathing | yoga at sunrise; person meditating calm
coffee | food | coffee|cafe|caffeine|espresso|latte | coffee being poured; cafe barista latte art
cooking | food | cooking|cook|recipe|recipes|kitchen|chef|chefs | chef cooking kitchen; cooking food pan | nprev=tim
restaurant | food | restaurant|restaurants|dinner|eating out|street food|food stall | restaurant dining table; street food stall
fast_food | food | fast food|burger|burgers|pizza|fries|junk food | burger and fries close up; pizza slice cheese
sugar | food | sugar|sweets|dessert|desserts|chocolate|cake | sugar cubes close up; chocolate dessert plate
groceries | food | groceries|grocery|supermarket|vegetables|fruits|fruit | supermarket aisle shopping; fresh vegetables market stall
alcohol | food | alcohol|drinking|beer|wine|whiskey|bar|pub|party | friends toasting drinks; bar counter drinks
family | people | family|families|parents|mom|dad|mother|father|kids|children|child|baby|son|daughter | family having dinner together; parents playing with kids
friends | people | friends|friendship|best friend|hanging out | friends laughing together; group of friends walking
couple | people | relationship|relationships|dating|girlfriend|boyfriend|wife|husband|marriage|married|wedding|breakup | couple holding hands; wedding ceremony couple
students | education | student|students|college|university|school|schools|classroom|exam|exams|degree|teacher|teachers|homework | students in classroom; university campus students
reading | education | book|books|reading|library|novel | person reading book; library bookshelves
learning | education | learn|learning|study|studying|course|courses|tutorial | student studying notes; person learning online laptop | generic
writing | education | writing down|handwriting|journal|journaling|notebook | hand writing notebook; journaling at desk
math | education | math|mathematics|statistics|calculate | math equations chalkboard; calculator and numbers
crime | crime | crime|criminal|criminals|murder|robbery|theft|stolen|kidnapping | police tape crime scene; detective investigation board | avoid=blood,gore,weapon,body
police | crime | police|cops|cop|arrest|arrested|investigation|detective|detectives|fbi|cbi | police car lights night; police officers street | avoid=blood,gore,weapon
court | law | court|courtroom|judge|judges|lawyer|lawyers|verdict|lawsuit|supreme court | courtroom gavel judge; lawyer in courthouse
prison | law | prison|jail|prisoner|prisoners|sentenced|bail | prison bars cell; jail corridor
news_media | news | news|journalist|journalists|reporter|headline|headlines|breaking news|newspaper | newspaper headlines; news anchor studio | proper
politics | news | government|politics|politician|politicians|election|elections|vote|voting|parliament|minister|president|prime minister|policy | government building exterior; people voting ballot box | proper
war_conflict | news | war|wars|conflict|army|military|soldiers|missile|missiles | military soldiers marching; army vehicles convoy | avoid=blood,gore,injury,body
protest | news | protest|protests|riot|riots | protest crowd signs; demonstrators street
football | sports | football|soccer|striker|premier league|world cup|messi|ronaldo | soccer ball stadium; football match players | sense=match:sport proper
cricket | sports | cricket|ipl|wicket|batsman|bowler|virat kohli|dhoni | cricket match stadium; cricket bat and ball | proper
basketball | sports | basketball|nba|dunk|lebron | basketball game court; basketball hoop slow motion | proper
sports_general | sports | sports|athlete|athletes|olympics|champion|championship|tournament|trophy | athlete training stadium; trophy celebration | generic
music | music | music|song|songs|singer|concert|album|guitar|piano|rapper|spotify | live concert crowd; musician playing guitar | proper
movie | culture | movie|movies|film|films|cinema|netflix|hollywood|bollywood|actor|actress | movie theater audience; film clapperboard | proper
art_design | culture | art|artist|painting|design|designer|drawing|creative|creativity | artist painting canvas; designer sketching
fashion | culture | fashion|clothes|clothing|outfit|outfits|shoes|sneakers|shopping | clothing store rack; fashion model walking
luxury | money | luxury|rich|wealthy|wealth|millionaire|millionaires|billionaire|billionaires|mansion|yacht|private jet | luxury mansion pool; private jet runway
poverty | money | poor|poverty|broke|homeless|slum|slums | empty wallet hands; person counting coins
success | emotion | success|successful|win|winning|achievement|goal|goals|dream|dreams|motivation|motivated | person celebrating success; mountain summit achievement | generic
failure | emotion | failure|fail|failed|mistake|mistakes|regret|lost everything | frustrated person laptop; crumpled paper failure | generic
happiness | emotion | happy|happiness|joy|smile|smiling|celebrate|celebration | people celebrating confetti; happy friends cheering | generic
loneliness | emotion | lonely|loneliness|alone|isolation|isolated | person alone window rain; lonely person bench
time_clock | time | time is money|years later|clock|ticking | clock ticking close up; timelapse clock | generic
morning_routine | lifestyle | morning routine|wake up|waking up|alarm|5am|early morning | alarm clock morning; morning sunlight bedroom
home | home | house|home|apartment|room|living room|bedroom | cozy living room; modern house exterior | generic
shopping_mall | city | mall|shopping mall|retail | shopping mall interior; people shopping bags
hospital_ambulance | health | ambulance|emergency|accident|accidents|injured | ambulance lights night; emergency room hospital | avoid=blood,gore,injury
dog_pet | animals | dog|dogs|puppy|puppies|cat|cats|kitten|pet|pets | dog playing park; cat relaxing sofa
wildlife | animals | wildlife|animals|lion|lions|tiger|tigers|elephant|elephants|birds | wildlife safari animals; elephant herd savanna
farming | nature | farm|farmer|farmers|farming|agriculture|crops|harvest | farmer working field; crop field harvest
india_context | city | india|indian|indians|bharat|rupee|rupees | india city street traffic; indian market crowd | generic proper
japan_context | city | japan|japanese|tokyo|kyoto|osaka | tokyo street night; kyoto temple | generic proper
usa_context | city | america|american|usa|united states|new york city | new york city skyline; american flag waving | generic proper
startup_pitch | business | sales pitch|investors|investor pitch|shark tank|pitch | entrepreneur pitching investors; startup pitch meeting | sense=pitch:business ctx=investor,investors,startup,business,deal,funding,shark amb=pitch
negotiation | business | deal|deals|contract|contracts|negotiate|negotiation|agreement|signed | business handshake deal; signing contract pen
growth_chart | finance | growth|grow|revenue|profit|profits|sales growth|graph|chart|charts | business growth chart rising; financial graph on screen | ctx=revenue,profit,sales,business,company,market,growth,money,numbers,percent amb=growth,grow,graph,chart,charts
"""


@dataclass(frozen=True)
class LexEntry:
    id: str
    domain: str
    triggers: tuple[tuple[str, ...], ...]
    queries: tuple[str, ...]
    sense: Optional[str] = None
    context: frozenset[str] = frozenset()
    generic: bool = False
    media: str = "either"
    avoid: tuple[str, ...] = ()
    duration_sec: Optional[float] = None
    ambiguous: frozenset[str] = frozenset()
    not_after: frozenset[str] = frozenset()
    proper_ok: bool = False


def _parse(raw: str) -> list[LexEntry]:
    entries: list[LexEntry] = []
    for line in raw.strip().splitlines():
        parts = [p.strip() for p in line.split(" | ")]
        if len(parts) < 4:
            continue
        ident, domain, trig, queries = parts[:4]
        opts = parts[4] if len(parts) > 4 else ""
        sense = None
        ctx: frozenset[str] = frozenset()
        avoid: tuple[str, ...] = ()
        dur = None
        amb: frozenset[str] = frozenset()
        nprev: frozenset[str] = frozenset()
        for opt in opts.split():
            if opt.startswith("sense="):
                sense = opt[6:]
            elif opt.startswith("ctx="):
                ctx = frozenset(w for w in opt[4:].split(",") if w)
            elif opt.startswith("avoid="):
                avoid = tuple(w for w in opt[6:].split(",") if w)
            elif opt.startswith("dur="):
                dur = float(opt[4:])
            elif opt.startswith("amb="):
                amb = frozenset(w for w in opt[4:].split(",") if w)
            elif opt.startswith("nprev="):
                nprev = frozenset(w for w in opt[6:].split(",") if w)
        triggers = tuple(
            tuple(t.split()) for t in (x.strip().lower() for x in trig.split("|")) if t
        )
        qs = tuple(q.strip() for q in queries.split(";") if len(q.strip().split()) >= 2)
        entries.append(LexEntry(
            id=ident, domain=domain, triggers=triggers, queries=qs[:3], sense=sense,
            context=ctx, generic="generic" in opts.split(),
            media="photo" if "photo" in opts.split() else "either",
            avoid=avoid, duration_sec=dur, ambiguous=amb, not_after=nprev,
            proper_ok="proper" in opts.split(),
        ))
    return entries


LEXICON: tuple[LexEntry, ...] = tuple(_parse(_RAW))
_DOMAIN_WORDS: dict[str, frozenset[str]] = {}
for _e in LEXICON:
    _DOMAIN_WORDS.setdefault(_e.domain, frozenset())
    _DOMAIN_WORDS[_e.domain] = _DOMAIN_WORDS[_e.domain] | frozenset(
        w for t in _e.triggers for w in t if len(w) > 3
    )

# Which domains suit which niche profile (a soft score boost, never a gate).
NICHE_DOMAINS: dict[str, frozenset[str]] = {
    "finance_business": frozenset({"finance", "money", "business", "work", "real_estate", "tech", "ai"}),
    "podcast_news": frozenset({"news", "business", "tech", "ai", "finance", "health", "science", "people", "social"}),
    "hindi_podcast": frozenset({"money", "finance", "people", "work", "news", "social", "city", "health"}),
    "education_tutorial": frozenset({"education", "science", "tech", "ai", "work", "space"}),
    "true_crime": frozenset({"crime", "law", "news", "city"}),
    "vlog_lifestyle": frozenset({"lifestyle", "food", "fitness", "health", "home", "people", "travel", "city"}),
    "travel_vlog": frozenset({"travel", "nature", "city", "food", "transport", "weather"}),
    "japan_travel_cinematic": frozenset({"travel", "nature", "city", "food", "transport", "culture"}),
}

# Spoken phrases that mean "look at what I am showing" — a cutaway there would
# hide the demonstration, so the planner keeps the source picture.
DEMONSTRATION_PHRASES: tuple[tuple[str, ...], ...] = tuple(tuple(p.split()) for p in (
    "look at this", "look at that", "as you can see", "you can see here", "you can see that",
    "check this out", "watch this", "on the screen", "on my screen", "right here",
    "this chart", "this graph", "this slide", "over here", "let me show you",
    "i'll show you", "take a look", "see this", "see that", "here you can see",
    "let's look at", "lets look at", "as shown", "this shows", "the line shows",
))
# Words of a live reaction — the speaker's face is the point; a cutaway there
# becomes a picture-in-picture instead.
REACTION_TOKENS = frozenset({
    "haha", "hahaha", "lol", "lmao", "omg", "wow", "whoa", "woah", "damn", "holy", "insane", "crazy", "wtf",
})

_SPACING_SEC = 5.0          # two lexicon anchors closer than this are one moment
_REPEAT_GAP_SEC = 45.0      # the same concept may recur only this far apart (not twice in one clip)
_CONTEXT_WINDOW_SEC = 10.0  # topic coherence window either side of the anchor
_SENSE_WINDOW_WORDS = 6     # trap-word context window (words either side)


def _norm(word: str) -> str:
    return re.sub(r"^[^a-z0-9&]+|[^a-z0-9&]+$", "", (word or "").lower())


def _token_matches(token: str, target: str, last: bool) -> bool:
    if token == target:
        return True
    if last and len(target) >= 3 and token in (target + "s", target + "es"):
        return True
    return False


def _phrase_at(tokens: list[str], i: int, phrase: tuple[str, ...]) -> bool:
    if i + len(phrase) > len(tokens):
        return False
    for k, part in enumerate(phrase):
        if not _token_matches(tokens[i + k], part, k == len(phrase) - 1):
            return False
    return True


def _words(transcript_words: list[dict[str, Any]], raw: Optional[list[str]] = None) -> tuple[list[str], list[float], list[float]]:
    tokens, starts, ends = [], [], []
    for w in transcript_words or []:
        text = w.get("word", w.get("text", ""))
        try:
            s = float(w.get("start", 0) or 0)
            e = float(w.get("end", s) or s)
        except (TypeError, ValueError):
            continue
        for piece in str(text).split():
            tokens.append(_norm(piece))
            if raw is not None:
                raw.append(piece)
            starts.append(s)
            ends.append(e)
    return tokens, starts, ends


def phrase_spans(transcript_words: list[dict[str, Any]],
                 phrases: tuple[tuple[str, ...], ...]) -> list[tuple[float, float]]:
    """(start, end) seconds of every occurrence of any phrase."""
    tokens, starts, ends = _words(transcript_words)
    spans = []
    for i in range(len(tokens)):
        for phrase in phrases:
            if _phrase_at(tokens, i, phrase):
                spans.append((starts[i], ends[min(i + len(phrase) - 1, len(ends) - 1)]))
    return spans


def reaction_times(transcript_words: list[dict[str, Any]]) -> list[float]:
    """Start times of reaction words and '!' exclamations."""
    out = []
    for w in transcript_words or []:
        raw = str(w.get("word", w.get("text", "")))
        if _norm(raw) in REACTION_TOKENS or "!" in raw:
            try:
                out.append(float(w.get("start", 0) or 0))
            except (TypeError, ValueError):
                pass
    return out


def lexicon_concepts(
    profile: dict[str, Any],
    transcript_words: list[dict[str, Any]],
    clip_duration: float,
    *,
    limit: int,
    exclude_near: tuple[float, ...] = (),
) -> list[dict[str, Any]]:
    """Ranked B-roll concept candidates for one clip (best first).

    ``limit`` caps how many candidates come back (the planner then places
    them under its own guards until the clip's cadence is met).
    ``exclude_near`` are anchors already used (e.g. the LLM plan's concepts):
    a candidate within ``_SPACING_SEC`` of one is skipped.
    """
    if limit <= 0 or clip_duration <= 0:
        return []
    raw: list[str] = []
    tokens, starts, _ends = _words(transcript_words, raw)
    if not tokens:
        return []
    niche = str(profile.get("niche") or profile.get("name") or profile.get("id") or "")
    preferred = NICHE_DOMAINS.get(niche, frozenset())

    candidates: list[tuple[float, float, LexEntry, str]] = []  # (score, anchor, entry, spoken)
    for i in range(len(tokens)):
        for entry in LEXICON:
            for phrase in entry.triggers:
                if not _phrase_at(tokens, i, phrase):
                    continue
                lo, hi = max(0, i - _SENSE_WINDOW_WORDS), min(len(tokens), i + len(phrase) + _SENSE_WINDOW_WORDS)
                nearby = set(tokens[lo:i]) | set(tokens[i + len(phrase):hi])
                if i > 0 and tokens[i - 1] in entry.not_after:
                    continue  # a name, not the concept ("Steve Jobs")
                if (
                    len(phrase) == 1 and not entry.proper_ok and i > 0
                    and raw[i][:1].isupper()
                    and not raw[i - 1].rstrip().endswith((".", "?", "!", ":"))
                ):
                    continue  # capitalised mid-sentence: a name ("Tim Cook"), not the noun
                needs_context = entry.context and len(phrase) == 1 and (
                    not entry.ambiguous or phrase[0] in entry.ambiguous)
                if needs_context and not (nearby & entry.context):
                    continue  # ambiguous single word without the context that fixes its sense
                anchor = starts[i]
                window = [t for t, s in zip(tokens, starts) if abs(s - anchor) <= _CONTEXT_WINDOW_SEC]
                coherence = sum(1 for t in window if t in _DOMAIN_WORDS.get(entry.domain, ()))
                if entry.generic and entry.domain not in preferred and coherence < 3:
                    continue  # a passing mention ("Sony in Japan" in a finance clip), not the topic
                # Niche fit outweighs phrase length: a travel vlog that
                # mentions "the stock market" in passing still shows the beach.
                score = (
                    1.0
                    + 0.4 * (len(phrase) - 1)
                    + 0.25 * min(4, max(0, coherence - 1))
                    + (1.0 if entry.domain in preferred else 0.0)
                    - (0.5 if entry.generic else 0.0)
                )
                spoken = " ".join(tokens[i:i + len(phrase)])
                candidates.append((score, anchor, entry, spoken))
                break  # one phrase per entry per position

    candidates.sort(key=lambda c: (-c[0], c[1]))
    chosen: list[tuple[float, float, LexEntry, str]] = []
    used_at: dict[str, list[float]] = {}
    for score, anchor, entry, spoken in candidates:
        if anchor >= clip_duration:
            continue
        if any(abs(anchor - a) < _SPACING_SEC for _s, a, _e, _sp in chosen):
            continue
        if any(abs(anchor - a) < _SPACING_SEC for a in exclude_near):
            continue
        if any(abs(anchor - a) < _REPEAT_GAP_SEC for a in used_at.get(entry.id, [])):
            continue
        chosen.append((score, anchor, entry, spoken))
        used_at.setdefault(entry.id, []).append(anchor)
        if len(chosen) >= limit:
            break

    out = []
    for score, anchor, entry, spoken in chosen:
        concept = {
            "anchor_sec": round(anchor, 3),
            "spoken": spoken,
            "concept": entry.queries[0],
            "search_queries": list(entry.queries),
            "sense": entry.sense,
            "media": entry.media,
            "avoid": list(entry.avoid),
            "cue_source": "lexicon",
            "lexicon_id": entry.id,
            "score": round(score, 2),
        }
        if entry.duration_sec is not None:
            concept["duration_sec"] = entry.duration_sec
        out.append(concept)
    return out
