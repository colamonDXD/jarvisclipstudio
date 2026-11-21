"""Widened guardrail evidence window (2026-11-12, niche lane #3).

The deterministic guardrails used to score only the transcript's
first ~2,000 chars — the same head sample the model prompt sees.
TJR's "What a $10M Trader Actually Does in a Day" spends that window
on a morning routine (wake-up, ginger shot, creatine); its trading
content starts around char 7,300 of 24,033, so even after the
day-trading dialect fix the finance correction could not fire for
the very video that exposed the bug. The finance lane pinned that
floor on purpose (the head scores 2, from the title alone) — the
floor is NOT lowered here either.

Instead, callers that hold the full transcript hand classify_niche
a second sample — guardrail_evidence_sample(): the head verbatim
plus a 1,000-char window centred on each quartile of the transcript
— used ONLY by the deterministic guardrails. The model prompt still
sees the head sample alone; no prompt changed, so no golden
re-pins. The podcast lock is computed over the same widened text: a
genuine podcast whose later segments range into trading (or
anywhere else) still locks, because its conversation cues sit in
the widened sample too. One deliberate exception, pinned below: the
success-path travel rule keeps scoring the head sample — its bar is
a destination title plus non-static visual evidence (a single title
keyword already reaches its score of 2), and feeding it late
off-topic segments would let a podcast guest's holiday story
re-file a podcast.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.auto_director.classify as classify_module
from src.auto_director.classify import (
    _NICHE_KEYWORDS,
    _hindi_evidence_score,
    _keyword_score,
    _podcast_locked,
    _travel_food_score,
    _vlog_format_score,
    apply_evidence_guardrails,
    classify_niche,
)


def _widen(transcript):
    """The builder, reached defensively so this file fails per-test
    (not at collection) on a tree where it does not exist yet."""
    builder = getattr(classify_module, "guardrail_evidence_sample", None)
    assert builder is not None, "guardrail_evidence_sample is missing"
    return builder(transcript)


def _model_podcast(confidence=0.82):
    return {
        "niche": "podcast_news",
        "confidence": confidence,
        "reasons": ["solo talking-head monologue"],
        "is_fallback": False,
        "evidence": {},
    }


def _install_podcast_agent(monkeypatch, prompts=None):
    """The substituted-agent seam answers podcast_news — the
    on-machine shape for every success-path fixture in this file."""
    mock_output = SimpleNamespace(
        niche="podcast_news",
        confidence=0.82,
        reasons=["solo talking-head monologue"],
    )

    async def _run(prompt):
        if prompts is not None:
            prompts.append(prompt)
        return SimpleNamespace(output=mock_output)

    mock_agent = SimpleNamespace(run=_run)
    monkeypatch.setattr(
        classify_module, "get_niche_classification_agent", lambda: mock_agent
    )
    return mock_agent


def _config():
    return SimpleNamespace(
        llm="openai:clip-fast",
        analysis_models=["openai:clip-assured"],
        analysis_fallback_model="groq:openai/gpt-oss-120b",
        analysis_direct_fallback_model="ollama:qwen3:8b",
        provider_mode="auto",
        local_llm="openai:local-qwen",
        provider_manager_enabled=True,
        openai_api_key="test-openai-key",
        openrouter_api_key=None,
        openrouter_fallback_model="anthropic/claude-haiku-4-5",
        google_api_key=None,
        anthropic_api_key=None,
        ollama_base_url="http://localhost:11434",
        ollama_api_key=None,
    )


def _install_all_fail(monkeypatch):
    """Every candidate build fails: classification must fall back —
    and the guardrails must still get their chance. (Same installer
    as the guardrails suite.)"""
    from src import provider_manager as pm

    pm._manager = pm.ProviderManager()
    monkeypatch.setattr(
        pm.ProviderManager, "_schedule_persist", lambda self, coro: coro.close()
    )
    monkeypatch.setattr(classify_module, "get_config", _config)
    monkeypatch.setattr(
        classify_module,
        "get_niche_classification_agent",
        lambda: (_ for _ in ()).throw(AssertionError("unused")),
    )
    monkeypatch.setattr(
        classify_module,
        "get_agent_for_model",
        lambda model, text_output=False: (_ for _ in ()).throw(
            RuntimeError("provider down")
        ),
    )
    monkeypatch.setattr(
        classify_module.provider_manager, "manager_enabled", lambda cfg=None: True
    )
    monkeypatch.setattr(
        classify_module.provider_manager,
        "run_recorded",
        AsyncMock(side_effect=AssertionError("no run without an agent")),
    )


_ACTIVITY_VISUAL = {
    "sample_count": 24,
    "face_presence_rate": 0.62,
    "multi_face_rate": 0.08,
    "mean_face_count": 0.71,
    "mean_largest_face_area_frac": 0.11,
    "cut_count": 42,
    "cuts_per_minute": 6.4,
    "span_seconds": 394.0,
    "mean_motion_spread": 0.48,
    "static_talking_head_likely": False,
    "scene_variety_high": True,
}

_STATIC_VISUAL = {
    "sample_count": 200,
    "face_presence_rate": 0.97,
    "multi_face_rate": 0.9,
    "mean_face_count": 1.9,
    "mean_largest_face_area_frac": 0.12,
    "cut_count": 4,
    "cuts_per_minute": 0.4,
    "span_seconds": 3600.0,
    "mean_motion_spread": 0.12,
    "static_talking_head_likely": True,
    "scene_variety_high": False,
}


# --- The target: the real $10M Trader transcript -----------------------------
#
# Verbatim caption transcript of TJR's "What a $10M Trader Actually
# Does in a Day" (24,033 chars), fetched by the finance lane. Its
# first 2,000 chars are a morning routine; the first trading word
# ("trader") appears at char ~7,315. This is the regression fixture
# for the widened window: it must classify finance_business through
# the guardrails once the guardrails can see past the opening.

REAL_10M_TITLE = "What a $10M Trader Actually Does in a Day"
REAL_10M_TRANSCRIPT = """I don't know if it's you. I can [music] tell the truth. I'm not in the mood. Yeah. Tell me how you feel. Tell me if it's real, but [music] that's nothing new. Yeah. That [&nbsp;__&nbsp;] together. I could be the go. I could be the to now. You trying to run a show. Trying to be my [music] man, but you know me too. Yeah. Good morning, guys. It's like 6:00 a.m. This is an early wake up. I'm trying to slowly but surely be able to wake up at like 5:30, 5:00 a.m. So, I keep just knocking the wake [clears throat] up time a little bit earlier. I usually wake up like anywhere from like 6 to 7 a.m. every single morning. But today, I'm going to bring you guys through little day in my life, a little behind the scenes of everything that I got going on cuz you guys see literally just a small portion of my life. So, today you're going to see everything. First and foremost, I just woke up and I have this crazy Oh my god. Like, I can I can't even turn my head to this side. So, I'm already [&nbsp;__&nbsp;] up. Like, I literally moving my head in any direction hurts so bad. And I'm also [&nbsp;__&nbsp;] sick. I feel like actual [&nbsp;__&nbsp;] And I just got better from feeling sick. I was like [&nbsp;__&nbsp;] myself and throwing up. And then I just got better from that. And now I have a cold. So my life's a [&nbsp;__&nbsp;] movie. And we're still up at 6:00 a.m. So hey man, [snorts] if I'm doing this, then you guys sure as hell can. This is I don't usually do this, but I don't know. I saw it in the fridge and at the like right now my body's on like 1%. Bro, this thing in my back is is [&nbsp;__&nbsp;] me up crazy right now. My throat is all [&nbsp;__&nbsp;] up, so we're adding extra steps, I guess. Cheers. What is What even is this ginger shot? [sighs] These are the three main things that I get going in the morning. We get a hydrogen tablet. Put that in some water. Some creatine. I do. If you're not mega dosing your creatine, you're stupid. I don't know [clears throat] where the thing where people were like, "Oh, you should take like five. What even is it? 5 g or 5 milligs?" Yeah, 5 g. I take like 15 or like 10. I learned this because [cough and clears throat] I can't remember who said it, but there was some study where it was like dudes that ended up taking 15 millig of creatine like ended up feeling better, performing better, whatever, put on more muscle than other dudes that like just took 5 g. And I'm also a really big guy. And I guess the bigger that you are, the more creatine that your body can hold and absorb. So last but not least, we have electrolytes. Sometimes or most of the time I'll do this, but we had these in the fridge and we're just switching [&nbsp;__&nbsp;] up today. So I guess this is what I get for traveling. And [clears throat] I was only gone for like a week, though. This routine will get me back though. You guys will see like the way that I run my days here. I love the way that I live here. But let me drink these things. We'll get outside. And to be honest, it feels kind of weird doing this with other people, but like I just like going outside in the morning and like just walking around my pool while I drink these [music] drinks. and I just kind of like breathe and chill and look around [music] because we live in Puerto Rico and it's beautiful here. So, that's what we're going to do. [music] What the hell? Honestly, getting in the hot tub right now sounds so good. So like and it was funny cuz right when I woke up and I felt my like neck and back all [&nbsp;__&nbsp;] up I was like damn maybe I should get in like the cold lunch and then I was like yeah [&nbsp;__&nbsp;] no off the wake up. I doubt that would even like make my [&nbsp;__&nbsp;] even feel better. It probably would slightly. We're doing uh Chad GPT doctor on my uh on my back. Chad GPT is like the absolute best. might have a muscle spasm or strain aggravated by my sleeping position. Probably both. It started hurting before I went to bed, but then after waking up today, my [&nbsp;__&nbsp;] is [&nbsp;__&nbsp;] up. Like, hurts really bad. So, it's saying skip lifting and it's just saying don't do anything. So, like this sucks. So, I was like I was hoping that it was going to tell me like one stretch that would just like fix it. But now you might as well just kill me, bro. Today we're off to a great start today. I did my little walk around, my little morning check up on my phone. Make sure that nothing's breaking or going wrong. Now, we're going to go for my walk. I really, really try to get 10,000 steps out of the way every single morning before I start my day. And the reason why I want to keep getting my wake up time earlier is because eventually I want to be able to get 10,000 steps and my gym session done for like the entire day. We're not quite there yet. Slowly but surely eventually I will be waking up at 5. Honestly, I need to just like say [&nbsp;__&nbsp;] it and just start doing it. But I don't know. I feel like this gradual thing has been working pretty well. So going to put my shoes on. Let's go get 10,000 steps. There's that [&nbsp;__&nbsp;] ass dog again. You guys remember that dog? &gt;&gt; I'm high key. Going to leave this [&nbsp;__&nbsp;] here. Bring a different water because there was one time I took a hydrogen tablet and it made me like [&nbsp;__&nbsp;] myself. I'm sorry. We're getting so crazy off like the first start of the video. But welcome to a day in my life. This is just normal. I took a hydrogen tablet and it literally made me just like go squirt everywhere and it was like so so bad. And that's kind of how this is making my stomach feel. I'm still going to drink this just later. I just don't want to go on like an hour and a half long walk and run into a little leaky butt situation. So, let's just leave that. &gt;&gt; A lot of times people ask me like why do you wake up [music] in the morning? And for most people, waking up this early sounds [music] like insane. I'm actually waking up earlier than most people that are going to work like a 9 to5. And I learned this from Alex Horoszi. It's probably one of the best lessons that I've ever learned. Pretty much my entire day is completely packed out, full schedule. And then on top of that, once everyone else starts waking up, I start getting text messages. I start getting emails. I start remembering that I have some meetings that I need to get on. and it just ends up being a whole [&nbsp;__&nbsp;] mess. That's the biggest problem is like once the day gets started, it's going to be impossible to knock out all the stuff that I personally either within the business want to do or physically like get my steps in, go to the gym, want to do. Luckily, I've been able to find ways to be able to, you know, like get the gym in during the day, but to be honest, I'm like not that good on my gym schedule. So, that's exactly why I'm trying to wake up even earlier. But starting my day off with just literally knocking out 10,000 steps has been massive for me. &gt;&gt; Cold Punch is lowkey calling my name. [&nbsp;__&nbsp;] it. We'll get in that [&nbsp;__&nbsp;] for the vlog. But this is this is a not regular day in my life. You got to want it, man. I just want it more. I just want it a whole lot more. See, that's why you'll see that's why you'll never see another trader doing the [&nbsp;__&nbsp;] that I do, man. I just don't want it bad enough. Matter of fact, the amount of times that I see traders get on their Instagram story talking about, "I have a cold. I'm not going to trade today." You're the type to not trade. You are the type to not trade. Me? [&nbsp;__&nbsp;] no. It's up at 5:30. Got to walk in at 6:00. It's up, man. It's up. Cold plunge at 8. Then I then I'm going to get in a shower. Guess what? Shower is going to be [&nbsp;__&nbsp;] freezing cold, too. You know why? Cuz I'm built different. I'm playing. I'm not. This shit's freezing. But hike feels really good on my back. I can feel it like soothing it. All right. Last but not least, let's do it. Man, we keep that [&nbsp;__&nbsp;] at 45°. I need to start doing that more cuz I feel incredible now. Every time I do it cuz like sometimes I will do it in the mornings and I'm every single time after I'm like, "Dude, I got to do this every morning. and this is the best feeling ever. And then when the morning comes around, it's like ah, I don't know if I want to do that. But that just made my back feel infinitely better. I still can't turn my head that much, but it made my back feel a lot better. I'm going to go shower, put something on to go trade. We're going to trade, see if we can make some money, and we'll keep going with our day. Juice cleanse beverage number one, boys. I And fun fact, I literally just liquid [&nbsp;__&nbsp;] myself. So, and I need to go again. So, that's exactly why we're doing this to fix whatever the [&nbsp;__&nbsp;] I got going on with myself. Let's do a taste test. This is what? It's only celery. &gt;&gt; Yeah. &gt;&gt; God. I'm just going to take like a shot of tequila. Can't be worse than that. Holy [&nbsp;__&nbsp;] that is horrible. God damn, I can taste the celery. Well, that's a good start. We'll have the rest of this when I'm done trading. [singing] &gt;&gt; [music] &gt;&gt; And that's a wrap. The stream done. It's now 11. The trade [music] is now back at break even. I'm trying to figure out what I want to do because we have high impact moves coming out at uh I think 2 or 3. I'm already down 28K for the day. I mean, I already took on risk by taking this trade. So, like I'm personally just going to leave this on, see what the [&nbsp;__&nbsp;] happens. We literally just went ended up going sideways on both the indexes after making this first initial move down. I personally thought we were going to grab these lows, which was my first trade. My first trade was a short position. That ended up getting stopped out and losing 28,000. Now I'm in this one down to the same same targets, same bias, all of that. Um, and we're just kind of floating around. We were moving down a little bit but got smoked. So now I need to figure out what the [&nbsp;__&nbsp;] I'm going to do with the rest of my day. I'm assuming I'm going to open up my phone to a whole bunch of texts and a whole bunch of people saying we need to get on meetings. So that's the game plan. Let's get it. Day in the life of a billionaire. Just drinking [&nbsp;__&nbsp;] bro. Everyone wants to be optimal, but when it comes to actually doing it, it sucks. Okay, so as you guys know, my back has been having issues. And thankfully, I have a freaking registered physical therapist that lives in my house, my girlfriend. So, she's going to try and figure out what's going on with me. &gt;&gt; Wait, before you before before you get &gt;&gt; what &gt;&gt; down? &gt;&gt; Oh, &gt;&gt; can you put your phone down? &gt;&gt; Sorry. &gt;&gt; Go down and touch. &gt;&gt; Oh, God. I don't know. &gt;&gt; And the penis where? &gt;&gt; It's right here. Like literally right here. &gt;&gt; Okay. And then hands on your hips. And then arch your back up and back. So, bend, shift forward. How's that? &gt;&gt; Fine. &gt;&gt; Okay. And then bend down to one side and the other side. &gt;&gt; Yeah. I feel it in that same spot that I showed you. &gt;&gt; Okay. Then the other side. &gt;&gt; Nothing. &gt;&gt; Okay. Go ahead and relax. I'm just GOING TO FEEL. &gt;&gt; WO. WO. WAIT. Be very soft. That's way too hard. That's way too hard. That was way too hard. Yeah. Yeah. &gt;&gt; It's like very sensitive. &gt;&gt; Tinder. &gt;&gt; Yeah. Very. Oh, yep. Right there is where that thing is. &gt;&gt; We're looking more at shoulder, not the back. &gt;&gt; Okay. We're looking at your shoulder. Okay. Tyler has a bit of tech neck. He's he sits at the desk all day. He has that rounded shoulder, head forward posture, which we all know when he was at the coding sink. So, okay. Come, come towards me, please. &gt;&gt; Wait. &gt;&gt; I need your head like it's going to hurt me. &gt;&gt; I need your head almost back. You can see how rounded Tyler's shoulders are. This one. [laughter] This one's a little higher than this one. &gt;&gt; I think I have scoliosis, too. Do I? I think I do. No, one of my shoulders is taller than the other one. I notice it all the time. &gt;&gt; I don't know. You'd have to get an X-ray, but I could see. Okay, just relax. &gt;&gt; Oh, that feels nice. All right, guys. It's funny cuz I was making fun of the people that do like regular day in my life vlogs and then it's like literally not how their day their life goes. Literally everything that we've done today besides trading has been irregular. Well, besides trading in the walk, got in the hot tub, got in the cold plunge, and now I'm getting like my I'm on [snorts] a freaking like massage table, but I feel way way better after all of that. I'm like not nearly as sensitive. I think that was the thing that was pissing me off most. I don't mind having some pain, but I don't like constantly bracing like my neck or my head from like turning one direction or another. So now that I can turn my head from like side to side at least somewhat comfortably before, like I literally couldn't. I was in so much pain. I feel a lot better. I wanted to go to the gym, but doctor said that I wasn't able to go. Okay. Well, in that case, when I don't go to the gym, what do I do? I go for another walk. So, guess where we're going? On another [&nbsp;__&nbsp;] walk. I'm not going to take you guys along with me on the second walk. We'll we'll talk to you guys after that, but I'm going to go for another walk right now. Get another 10,000 steps so that I can hit 20,000 steps today. And then um we'll sit down. We'll have another juice. I'll talk to you guys a little bit. I'm going to film a YouTube video. That's the sad thing about the days in my life. They are consistently boring. But having those consistently boring days of like doing the same [&nbsp;__&nbsp;] over and over and over again, that's what's going to get you infinitely better than the competition. We're going to work on our mind, our body, and our money. Okay, we made it back from the walk and I just realized on the walk that the whole point of this vlog, this isn't even a vlog. This is It's felt like a vlog. This is supposed to be motivating you guys. So, let's like lock the [&nbsp;__&nbsp;] in and like let's give you guys some [&nbsp;__&nbsp;] sauce cuz literally this entire day has been me chilling in the hot tub trading. Oh [&nbsp;__&nbsp;] we need to see what happened with the trade. I set my stop loss in and take profit. It's three. So, this is actually when interest rates are supposed to come out. Everyone is already assuming that there's going to be no change. But, let's check on the trade. And this actually leads into exactly what I wanted to talk about, which is losing days within trading and also like just motivation as a whole within trading because I lost my first trade today. And notice there is nothing wrong with me. I am not crashing out. I'm not punching holes in the wall. I'm not screaming and crying like you guys do when you blow your funds. Okay, so we're going to talk about how to deal with losses. Let's see if we ended up winning today or if this was a losing day. If we end up winning, it kind of contradicts the whole motivational speech that I'm about to give. But I'm kind of assuming that I lost. So, let's see. Okay, so today we did end up losing both trades. I ended up losing 52,835 bucks. So, this is perfect for exactly what I want to talk to you guys about, which is just losing trades and kind of the whole business of trading as a whole. I like to relate trading to a business because it is a business. Okay, within a business, let's say I run a clothing brand for for example, I have to make a payment for inventory. How much is that going to cost me? Let's say it cost me $50,000. But me taking that loss and sending that business payment is eventually going to probably make me who knows, like an extra $100,000 if my margins are good. And then I have to pay employees. So, that's going to be a payment out as well. that loss is just like me paying for inventory or that loss is just like me paying for employees that work in my business. Why is that the case? Because in trading, your profit margins are 100% besides the small little fees that you pay for different sort of subscriptions like your Trading View subscription and whatever the brokerage fees that you're trading are on. You know, it's very very low. Your margins are essentially 100%. So the only losses in expenses that you are actually taking are going to be the losses that you take throughout the month. The only way for me to be able to create and get big wins is by also taking those losses. Why? Because when my strategy presents itself in the market, what do I have to do? I have to execute on that strategy 100% of the time. Is my strategy going to win 100% of the time? No, obviously not. It just gives me probability that I'm going to be right more often than I lose. And when I'm right, my wins are going to be larger than my losers. For example, yesterday I took a trade and I made $96,000. I compare that to today's loss, I lost $56,000. So technically, if we group both these days together, I'm up $4ome,000. But a lot of people don't see it like that. They see it on just like, god damn it, today I lost $56,000. We need to expand how we look at trading and our P&amp;Ls as a whole and our honestly like entire lifetime P&amp;L as a trader across a much larger timeline instead of focusing on the individual outcomes as a trader. Because if I sat myself down and I said, "Damn, I lost 56K today. How can I make this back right the [&nbsp;__&nbsp;] now? I'm freaking the [&nbsp;__&nbsp;] out. This is the worst day of my life." Then what's that going to cause me to do? It's going to cause me to take more trades. It's going to cause me to overlever. which is going to talk cause me to make poor decisions that is going to dig me deeper into a hole. The same thing as you guys when you guys are trading on these funded accounts. You guys buy a funded account, you guys might pass the eval. You guys might even be like one or two days away from getting a payout, but then you guys you fail that funded account. When you guys fail on that funded account, you guys start freaking out. You guys are like, "Oh my gosh, I was two winning days away from getting a payout. I should have had that." And then what happens? You go buy three more funded accounts and then you blow all of them trying to chase that win. No, we need to zoom out on like a month month's worth of time on a year's worth of time and see, okay, am I profitable over this large range and this large time period? Because if we focus on the individual outcomes of every single day within trading, we're never going to be a winner because if we need to win every single day, it's just impossible within trading. So, this was like a prime time example that I've I've always wanted to tell you guys about where like we need to focus on the larger long-term outcomes like, hey, am I making money on a monthly basis? If the answer is yes, like for me, this this year I haven't had a single losing month. That's actually like I think a record for me. I'm pretty sure most years I always have at least one or two losing months. This year I at least to my knowledge I have not had a singular losing month which is unbelievable, right? But if I sat down and focused and and focused on every single loss that I took throughout this entire year, I guarantee you I promise you that I would probably have multiple losing months. And if I was trying to chase those losses that I was taking on a daily basis and focus on those individual outcomes of those individual losing days, then I would be in a much worse position than I'm at today. So, what I want you guys to do is instead of saying this loss is horrible, no, that loss actually helped you get your next win. Because when you execute on your strategy 100% of the time with proper rules, with proper risk, you're giving yourself the opportunity to get those wins. So when I took those two trades today, did I lose both of them? Yes. But what is that also giving me the potential, the opportunity, the probability to be able to do to be able to win one trade worth $96,000 in profit? This loss today or these two losses today are actually helping me get closer to my next win that I know I'm sounds stupid, but I'm due on due to my probabilities within the market. And I know that, you know, 10 times out of 10 when I execute on my strategy, I have higher odds of winning than losing. And I know that when I win, my winners are going to be larger than my losers. And that's all that I care about. So whenever my strategy presents itself, I'm taking that [&nbsp;__&nbsp;] trade. Why? Because I know odds are in my favor and statistics are going to be favoring me. So that's a wrap for today's uh trading day. It's 3 now. Um I've I'm already at 20,000 steps. I drank a bunch of that [&nbsp;__&nbsp;] juice. I'm about to take my fourth [&nbsp;__&nbsp;] of the day. Welcome to a day in my life. But I'm about to go film another where YouTube Inception, right? Isn't that Isn't that the movie Inception where they're in a dream within a dream within a dream? I'm going to film a YouTube video on my desktop while we're filming this YouTube video. A little BTS sort of thing. So, I'm going to go film that. That's going to be a fire video. Wait, I had I had the perfect idea for it, but now I'm like blanking on it. Oh, yeah. It's on funded accounts. Okay, cool. So, I'm going to go film that right now. You guys will see a little behind the scenes action of that and then hopefully by that time the cars will be done. I'll probably need a [&nbsp;__&nbsp;] again. We'll take you guys on a drive. Anyways, let's go film this YouTube video. Got to make sure the hook's good or else you idiots don't watch it. So, we got to So, this is We got to literally make sure that this [&nbsp;__&nbsp;] is [music] perfect, or else you guys will be like, "No, next. I know I'm going to drop some [&nbsp;__&nbsp;] bars, so I need to get your attention first." In this video, I'm going to be showing you guys the best way to trade funded accounts as a beginner [music] trader. I'm going to be covering what prop firms you guys should use, what accounts on each prop firm you guys should be using, and then also how to pass the evals, and then last but not least, how to get a payout. And I'm also going to share the strategy that I use personally when I'm trading on these funded accounts. So with that being said, let's get right into it. First, we need to understand what a profession. I think [music] I want to take a ride. I think you could be my boys. Just got back having my last drinks of juice for the night and that's honestly going to be a wrap for the day. I'm about [music] to do a little steak stream. We're going to be doing some gambling. So, that's going to be fun. That's a full day in my [music] life. Man, we did a lot of [&nbsp;__&nbsp;] today. We filmed three YouTube videos. this one that you're you guys are watching right now, a desktop YouTube video, and we just went and filmed another YouTube video of me like driving in the Bravis, like some different [&nbsp;__&nbsp;] that we've never [music] done before. Three YouTube videos done, 25,000 steps, shot myself four times, motivated you guys three times, and I'm [music] about to go gamble. If I don't do it, who will? You know what I'm saying? And guess what? I get to wake up tomorrow and do it all over again. We're probably just going to wrap it here. I'm going to do the steak. get all fired up, gamble, hit the [&nbsp;__&nbsp;] slot machine. But that's where I'm going to leave you guys for today. I love and appreciate you guys. If you enjoyed this video, drop a subscribe. We might do more of these. And I'll catch you guys in the next one. [music] Peace."""


def _finance_margin(title, sample):
    """(finance score, margin over the strongest rival) scored the
    way _finance_evidence_correction scores them."""
    finance = _keyword_score(
        _NICHE_KEYWORDS["finance_business"], title, sample
    )
    rivals = {"travel_vlog": _travel_food_score(title, sample)}
    for niche, keywords in _NICHE_KEYWORDS.items():
        if niche != "finance_business":
            rivals[niche] = _keyword_score(keywords, title, sample)
    rivals["hindi_podcast"] = _hindi_evidence_score(title, sample)[0]
    return finance, finance - max(rivals.values())


class TestGuardrailEvidenceSample:
    def test_short_transcript_returned_unchanged(self):
        text = "Welcome to another vlog. We are going to Miami today."
        assert _widen(text) == text
        assert _widen("  padded short text  ") == "padded short text"

    def test_none_and_empty(self):
        assert _widen(None) == ""
        assert _widen("") == ""
        assert _widen("   ") == ""

    def test_head_is_the_verbatim_first_chunk(self):
        full = REAL_10M_TRANSCRIPT.strip()
        assert _widen(full).startswith(full[:2000])

    def test_quartile_windows_are_sampled(self):
        text = "lorem " * 4000  # exactly 24,000 chars
        assert len(text) == 24000

        def splice(source, pos, word):
            return source[:pos] + " " + word + " " + source[pos + len(word) + 2:]

        text = splice(text, 6000, "alphamarker")
        text = splice(text, 12000, "betamarker")
        text = splice(text, 18000, "gammamarker")
        text = splice(text, 3000, "nearmarker")
        text = splice(text, 22000, "tailmarker")
        sample = _widen(text)
        # One marker at each quartile centre is inside its window.
        assert "alphamarker" in sample
        assert "betamarker" in sample
        assert "gammamarker" in sample
        # Text between the head and the first window, and past the
        # last window, is NOT sampled: the sample is stratified, not
        # the whole transcript.
        assert "nearmarker" not in sample
        assert "tailmarker" not in sample
        assert sample.startswith(text[:2000])

    def test_total_size_is_capped(self):
        full = REAL_10M_TRANSCRIPT.strip()
        sample = _widen(full)
        # Head + three windows + separators, never the full text.
        assert 4000 < len(sample) <= 2000 + 3 * 1000 + 3
        assert len(sample) < len(full)

    def test_mid_length_transcript_is_not_duplicated(self):
        text = "b" * 2600
        sample = _widen(text)
        # Head + the one tail chunk, joined once: every char of the
        # original appears exactly once.
        assert sample.count("b") == 2600
        assert len(sample) == 2601


class TestWidenedTarget10M:
    def test_fixture_hygiene_scores(self):
        """Why the classify tests below can pass: over the head the
        finance score is under the floor (the finance lane's pin);
        over the widened sample it clears the floor AND the margin,
        and nothing in the full transcript trips the podcast lock."""
        full = REAL_10M_TRANSCRIPT.strip()
        head = full[:2000]
        head_finance, _ = _finance_margin(REAL_10M_TITLE, head)
        assert head_finance < 4
        widened = _widen(full)
        finance, margin = _finance_margin(REAL_10M_TITLE, widened)
        assert finance >= 4
        assert margin >= 2
        assert _podcast_locked(REAL_10M_TITLE, widened, None) is False
        # The widened sample really does reach the funded-account
        # passage (char ~18,123) that the head never sees.
        assert "funded account" in widened
        assert "funded account" not in head

    @pytest.mark.asyncio
    async def test_success_path_corrects_to_finance(self, monkeypatch):
        _install_podcast_agent(monkeypatch)
        full = REAL_10M_TRANSCRIPT.strip()
        result = await classify_niche(
            full[:2000], [], title=REAL_10M_TITLE,
            guardrail_sample=_widen(full),
        )
        assert result["niche"] == "finance_business"
        assert result["is_fallback"] is False
        assert 0.55 <= result["confidence"] <= 0.7
        assert (
            result["evidence"]["guardrail"]
            == "podcast_news_overridden_by_finance_evidence"
        )

    @pytest.mark.asyncio
    async def test_success_path_without_widened_sample_stays_podcast(
        self, monkeypatch
    ):
        """The control: same call, no guardrail_sample — the head
        alone still cannot fire the correction. This is the base
        behaviour the widened sample changes, and only there."""
        _install_podcast_agent(monkeypatch)
        full = REAL_10M_TRANSCRIPT.strip()
        result = await classify_niche(full[:2000], [], title=REAL_10M_TITLE)
        assert result["niche"] == "podcast_news"
        assert "guardrail" not in result["evidence"]

    @pytest.mark.asyncio
    async def test_failure_path_rescues_to_finance(self, monkeypatch):
        _install_all_fail(monkeypatch)
        full = REAL_10M_TRANSCRIPT.strip()
        result = await classify_niche(
            full[:2000], [], title=REAL_10M_TITLE,
            guardrail_sample=_widen(full),
        )
        assert result["niche"] == "finance_business"
        assert result["is_fallback"] is True
        assert result["guardrail_rescued"] is True

    def test_direct_guardrail_with_activity_visual(self):
        full = REAL_10M_TRANSCRIPT.strip()
        out = apply_evidence_guardrails(
            _model_podcast(), title=REAL_10M_TITLE,
            transcript_sample=full[:2000],
            evidence_sample=_widen(full),
            visual=_ACTIVITY_VISUAL, audio=None,
        )
        assert out["niche"] == "finance_business"

    @pytest.mark.asyncio
    async def test_prompt_still_sees_only_the_head_sample(self, monkeypatch):
        """The widened sample is guardrail-only evidence: the model
        prompt is built from the head sample exactly as before."""
        prompts = []
        _install_podcast_agent(monkeypatch, prompts=prompts)
        full = REAL_10M_TRANSCRIPT.strip()
        await classify_niche(
            full[:2000], [], title=REAL_10M_TITLE,
            guardrail_sample=_widen(full),
        )
        assert len(prompts) == 1
        assert full[:200] in prompts[0]
        # "funded account" (char ~18,123) reaches the guardrails via
        # the third window; it must NOT reach the model.
        assert "funded account" not in prompts[0]


# --- Trap (a): a genuine podcast whose later segments are trading-dense ------
#
# A two-person interview ABOUT trading: the opening is pure show
# format (host, guest, episode), the back half is the guest's
# trading talk, as dense as the real monologue. Over the widened
# sample the finance evidence is strong — the ONLY thing protecting
# this fixture is the podcast lock computed over the same widened
# text. That is exactly the precision the widening had to survive.

TRADING_EXCERPT = (
    "Okay, so today we did end up losing both trades. I ended up "
    "losing 52,835 bucks. Because in trading, your profit margins are "
    "100% besides the small little fees that you pay for different "
    "sort of subscriptions like your Trading View subscription and "
    "whatever the brokerage fees that you're trading are on. So the "
    "only losses in expenses that you are actually taking are going "
    "to be the losses that you take throughout the month."
)
FUNDED_EXCERPT = (
    "The same thing as you guys when you guys are trading on these "
    "funded accounts. You guys buy a funded account, you guys might "
    "pass the eval. You guys might even be like one or two days away "
    "from getting a payout, but then you guys you fail that funded "
    "account. Because if we focus on the individual outcomes of every "
    "single day within trading, we're never going to be a winner."
)

FINANCE_PODCAST_LATE_TITLE = "Conversations at the Desk — The Veteran Trader"
FINANCE_PODCAST_LATE_HEAD = (
    "Welcome back to the show. I'm your host, Daniel, and today my "
    "guest is a professional trader who has spent fifteen years "
    "working on a dealing desk in the city. In this episode we talk "
    "about how he got started, the mentors who shaped him, and what "
    "an ordinary morning looks like before the opening bell. We "
    "recorded this conversation in our studio last week, so let's "
    "get into it. Tell me, Daniel asks, when did you first realise "
    "this was the career for you? Well, he says, I was stacking "
    "shelves in a supermarket and a friend showed me a chart on his "
    "phone, and something just clicked. We go on to talk about his "
    "childhood, his first job, the years he spent learning from "
    "older colleagues, and how the floor has changed since "
    "everything went electronic. It is a wide-ranging chat about a "
    "life spent around markets, recorded over two coffees on a "
    "rainy Tuesday afternoon in the studio, with the rain audible "
    "on the windows whenever the conversation pauses."
)
_INTERVIEW_FILLER = (
    "He laughs and says the neighbourhood has changed a lot since "
    "he moved here twenty years ago. We talk about the commute, the "
    "coffee place on the corner that everyone queues at in the "
    "morning, and the park where he walks his dog before work. His "
    "sister lives two streets away, and Sunday lunches at her place "
    "are a family tradition nobody is allowed to skip. He shows me "
    "photos of the house he grew up in, the garden his father still "
    "tends, and the motorbike he keeps meaning to fix one of these "
    "weekends. The conversation drifts to books, the novel he is "
    "halfway through, and a documentary series everyone in the "
    "office keeps recommending to him. I ask about the people who "
    "taught him the job, and he names three of them, telling a "
    "story about each one, including the morning the fire alarm "
    "emptied the whole building mid-shift and everyone ended up "
    "standing in the street in the rain, still arguing about the "
    "football results from the night before."
)
_TRADING_BLOCK = TRADING_EXCERPT + " " + FUNDED_EXCERPT
FINANCE_PODCAST_LATE_TRANSCRIPT = (
    FINANCE_PODCAST_LATE_HEAD
    + " " + _INTERVIEW_FILLER
    + " So let's talk about the craft itself. " + _TRADING_BLOCK
    + " And when the month turns against you? " + _TRADING_BLOCK
    + " What about the risk side of it? " + _TRADING_BLOCK
    + " One more pass over the numbers, then. " + _TRADING_BLOCK
)


class TestTrapFinancePodcastLate:
    def test_fixture_hygiene_lock_and_strength(self):
        """The trap is real: finance evidence over the widened
        sample clears the correction's floor — and the lock, fed
        the same widened text, holds."""
        widened = _widen(FINANCE_PODCAST_LATE_TRANSCRIPT)
        finance, margin = _finance_margin(FINANCE_PODCAST_LATE_TITLE, widened)
        assert finance >= 4
        assert margin >= 2
        assert _podcast_locked(FINANCE_PODCAST_LATE_TITLE, widened, None) is True

    @pytest.mark.asyncio
    async def test_success_path_stays_podcast(self, monkeypatch):
        _install_podcast_agent(monkeypatch)
        result = await classify_niche(
            FINANCE_PODCAST_LATE_TRANSCRIPT[:2000], [],
            title=FINANCE_PODCAST_LATE_TITLE,
            guardrail_sample=_widen(FINANCE_PODCAST_LATE_TRANSCRIPT),
        )
        assert result["niche"] == "podcast_news"
        assert "guardrail" not in result["evidence"]

    @pytest.mark.asyncio
    async def test_failure_path_stays_podcast(self, monkeypatch):
        _install_all_fail(monkeypatch)
        result = await classify_niche(
            FINANCE_PODCAST_LATE_TRANSCRIPT[:2000], [],
            title=FINANCE_PODCAST_LATE_TITLE,
            guardrail_sample=_widen(FINANCE_PODCAST_LATE_TRANSCRIPT),
        )
        assert result["niche"] == "podcast_news"
        assert result.get("guardrail_rescued") is not True

    def test_direct_guardrail_with_activity_visual_stays_podcast(self):
        out = apply_evidence_guardrails(
            _model_podcast(), title=FINANCE_PODCAST_LATE_TITLE,
            transcript_sample=FINANCE_PODCAST_LATE_TRANSCRIPT[:2000],
            evidence_sample=_widen(FINANCE_PODCAST_LATE_TRANSCRIPT),
            visual=_ACTIVITY_VISUAL, audio=None,
        )
        assert out["niche"] == "podcast_news"
        assert "guardrail" not in out["evidence"]


# --- Trap (c): a genuine podcast that name-drops travel/food late ------------
#
# A business interview whose guest spends the back half telling
# holiday stories from a Japan trip. Over the widened sample the
# travel keywords are dense — a naive widening would hand the
# success-path travel rule the score it needs. It keeps scoring the
# HEAD sample (its bar is title + visual; the transcript was never
# its gate), and the failure path is protected by the lock computed
# over the widened text. Both are pinned here.

TRAVEL_PODCAST_TITLE = "The Hustle Show — Episode 88: Building a Coffee Brand"
TRAVEL_PODCAST_HEAD = (
    "Welcome back to the show. Today my guest is Maya, who built a "
    "coffee brand from a single market stall into a company that "
    "supplies cafes in four cities. In this episode we talk about "
    "the early years, the roastery she built in a rented warehouse, "
    "and what she looks for when she hires. We recorded this in the "
    "studio last month, so let's get into the conversation. Maya, "
    "take me back to the very first bag of beans you ever sold. She "
    "laughs and says it was to her neighbour, who paid in cash and "
    "asked for a second bag the same week. From there we talk about "
    "packaging, the logo she drew on a napkin, the first wholesale "
    "order that nearly sank the company, and the mentor who told "
    "her to raise her prices and stop apologising for them. It is a "
    "story about patience, stubbornness, and a lot of early "
    "mornings at the roastery with the radio on."
)
_COFFEE_FILLER = (
    "We talk about the beans themselves for a while, where they "
    "come from, how the harvest changes from year to year, and why "
    "she pays more for the lots she likes even when money is tight. "
    "She describes the first machine she bought second-hand, the "
    "lever she had to hold down with her knee, and the regulars who "
    "still ask for her by name. Then the conversation turns to the "
    "shop she opened last year, the mural on the wall that a local "
    "artist painted for free, and the queue that forms on Saturday "
    "mornings before the doors even open. Her team is twelve people "
    "now, and she interviews every one of them herself, usually "
    "over a long coffee at the counter while the morning rush "
    "happens around them."
)
_COFFEE_FILLER_2 = (
    "I ask how she picks the people who work the counter, and she "
    "says she can tell in five minutes whether someone will be kind "
    "to a stranger who is having a bad morning. We talk about the "
    "landlord who nearly doubled the rent in the second year, the "
    "winter the machine broke three times in a month, and the "
    "accountant who finally made her sit down and look at the "
    "numbers properly. She is honest about the mistakes: the second "
    "location that was too far from the first, the fancy cups that "
    "chipped, the staff party that ended with everyone in the "
    "roastery at midnight finishing an order by hand. By now the "
    "studio has gone quiet around us, and she is relaxed enough to "
    "tell the story of the health inspector who became a regular, "
    "which still makes her laugh every time she tells it."
)
_TRAVEL_BLOCK = (
    "Last spring I finally took the trip I had been saving for. We "
    "landed in Tokyo and spent the first evening wandering between "
    "tiny restaurants near the station, tasting whatever the chefs "
    "put in front of us. The street food in Japan is on another "
    "level — the food tour we booked on the second day took us "
    "through three neighbourhoods, and the guide kept stopping at "
    "stalls I would never have found on my own. Our itinerary had "
    "one free day, so we took the train out to a smaller town, "
    "walked the old streets, and ate the best noodles of my life "
    "in a six-seat place with no sign outside. The cuisine changes "
    "completely as you travel between regions; every destination "
    "seems to have one dish it refuses to share with the rest of "
    "the country."
)
TRAVEL_PODCAST_TRANSCRIPT = (
    TRAVEL_PODCAST_HEAD
    + " " + _COFFEE_FILLER
    + " " + _COFFEE_FILLER_2
    + " You travel a lot for work — any trips for pure pleasure? "
    + _TRAVEL_BLOCK
    + " And the second week of that trip? " + _TRAVEL_BLOCK
    + " One last food memory before we wrap up. " + _TRAVEL_BLOCK
)


class TestTrapTravelPodcastLate:
    def test_fixture_hygiene(self):
        """The trap is real: travel evidence over the WIDENED sample
        clears the travel rule's score of 2 — only the head-scoped
        travel rule (and the lock, on the failure path) protects
        this fixture."""
        widened = _widen(TRAVEL_PODCAST_TRANSCRIPT)
        assert _travel_food_score(
            TRAVEL_PODCAST_TITLE, TRAVEL_PODCAST_TRANSCRIPT[:2000]
        ) < 2
        assert _travel_food_score(TRAVEL_PODCAST_TITLE, widened) >= 2
        assert _podcast_locked(TRAVEL_PODCAST_TITLE, widened, None) is True

    def test_direct_guardrail_with_activity_visual_stays_podcast(self):
        """Even with an activity visual — the travel rule's other
        bar — the late travel talk cannot steal the podcast,
        because the travel rule scores the head sample."""
        out = apply_evidence_guardrails(
            _model_podcast(), title=TRAVEL_PODCAST_TITLE,
            transcript_sample=TRAVEL_PODCAST_TRANSCRIPT[:2000],
            evidence_sample=_widen(TRAVEL_PODCAST_TRANSCRIPT),
            visual=_ACTIVITY_VISUAL, audio=None,
        )
        assert out["niche"] == "podcast_news"
        assert "guardrail" not in out["evidence"]

    def test_direct_guardrail_with_static_visual_stays_podcast(self):
        out = apply_evidence_guardrails(
            _model_podcast(), title=TRAVEL_PODCAST_TITLE,
            transcript_sample=TRAVEL_PODCAST_TRANSCRIPT[:2000],
            evidence_sample=_widen(TRAVEL_PODCAST_TRANSCRIPT),
            visual=_STATIC_VISUAL, audio=None,
        )
        assert out["niche"] == "podcast_news"

    @pytest.mark.asyncio
    async def test_failure_path_stays_podcast(self, monkeypatch):
        _install_all_fail(monkeypatch)
        result = await classify_niche(
            TRAVEL_PODCAST_TRANSCRIPT[:2000], [],
            title=TRAVEL_PODCAST_TITLE,
            guardrail_sample=_widen(TRAVEL_PODCAST_TRANSCRIPT),
        )
        assert result["niche"] == "podcast_news"
        assert result.get("guardrail_rescued") is not True


# --- Fixture (b): Week-in-the-Life at full length -----------------------------
#
# The vlog sibling at realistic length: the head fixture verbatim,
# then lifestyle monologue for the rest of the runtime. The vlog
# rule fired on the head alone before this lane; the widened sample
# must not change its outcome on either path, and the finance
# scorer must still read exactly 0 over the widened sample (the
# real video's finance score is 0 — the trading identity lives in
# its description links, never in the speech).

WIL_TITLE = "Week in the Life of a Millionaire in Los Angeles"
WIL_HEAD = (
    "What is up, boys? Welcome to another vlog. This is kind of going "
    "to be all over the place and I honestly don't even know where "
    "it's going to end up. We are currently in the process of going "
    "to Miami. I'm going to get a haircut. Then, we're going to get "
    "on a flight, go to California, and then I'll probably vlog a "
    "bit of me being back home."
)
_WIL_BLOCK_A = (
    "Back home I made a smoothie, answered a stack of emails, and "
    "filmed a quick update for the channel on the kitchen counter. "
    "Then it was calls back to back until lunch — my assistant ran "
    "through the week with me, a shoot on Thursday, a dinner with "
    "some brand people on Friday, and a flight to Miami on Sunday "
    "morning for a friend's birthday. I squeezed in a haircut, "
    "picked up the dry cleaning, and spent an hour just walking "
    "around the neighbourhood with the camera, talking through "
    "ideas for the next few videos and whether the morning routine "
    "series is worth continuing."
)
_WIL_BLOCK_B = (
    "The afternoon was gym session number two, legs, which I always "
    "put off, then a long drive across the city to see a friend who "
    "just moved into a new place. We ordered takeout, sat on his "
    "balcony until the sun went down, and talked about nothing "
    "important for hours — old stories, terrible jokes, the usual. "
    "On the way home I stopped for groceries, called my mom, and "
    "ended the night editing on the couch with a movie playing in "
    "the background and a bowl of popcorn going cold beside me."
)
_WIL_BLOCK_C = (
    "Wednesday started slow. Coffee on the balcony, a bit of "
    "journaling, then a deep clean of the apartment because the "
    "mess was starting to annoy me. I filmed a look around the new "
    "place for an upcoming video, showed the desk setup, the couch "
    "corner where I read, and the shelf of sneakers I keep saying "
    "I will thin out. In the evening I met two friends downtown, "
    "we grabbed burgers and milkshakes, and I was home by ten, "
    "plotting out tomorrow's shoot and charging every battery I own."
)
WIL_TRANSCRIPT = (
    WIL_HEAD + " " + _WIL_BLOCK_A + " " + _WIL_BLOCK_B + " "
    + _WIL_BLOCK_C + " " + _WIL_BLOCK_A + " " + _WIL_BLOCK_B
)


class TestWeekInTheLifeUnchanged:
    def test_fixture_hygiene(self):
        widened = _widen(WIL_TRANSCRIPT)
        assert _keyword_score(
            _NICHE_KEYWORDS["finance_business"], WIL_TITLE, widened
        ) == 0
        assert _vlog_format_score(WIL_TITLE, widened) >= 3
        assert _podcast_locked(WIL_TITLE, widened, None) is False

    @pytest.mark.asyncio
    async def test_success_path_stays_vlog(self, monkeypatch):
        _install_podcast_agent(monkeypatch)
        result = await classify_niche(
            WIL_TRANSCRIPT[:2000], [], title=WIL_TITLE,
            guardrail_sample=_widen(WIL_TRANSCRIPT),
        )
        assert result["niche"] == "vlog_lifestyle"
        assert result["is_fallback"] is False

    @pytest.mark.asyncio
    async def test_failure_path_stays_vlog(self, monkeypatch):
        _install_all_fail(monkeypatch)
        result = await classify_niche(
            WIL_TRANSCRIPT[:2000], [], title=WIL_TITLE,
            guardrail_sample=_widen(WIL_TRANSCRIPT),
        )
        assert result["niche"] == "vlog_lifestyle"
        assert result["is_fallback"] is True
        assert result["guardrail_rescued"] is True


# --- Equivalence: a guardrail sample equal to the head changes nothing --------

GAMING_TITLE = "RANKED GRIND — New Raid Speedrun Gameplay"
GAMING_TRANSCRIPT = (
    "Welcome back to the ranked grind. In this gameplay breakdown we "
    "speedrun the new raid, clutch the final boss, and test the patch "
    "notes loadout. My K/D this season is unreal."
)
HINDI_BUSINESS_TITLE = "व्यापार की असली सच्चाई — जो कोई नहीं बताता"
HINDI_BUSINESS_TRANSCRIPT = (
    "दोस्तों, व्यापार में सबसे बड़ी गलती यह होती है कि लोग जल्दी अमीर बनना "
    "चाहते हैं। मेरा अपना अनुभव यह कहता है कि धीरे-धीरे बढ़ने वाला काम ही "
    "टिकता है। गाँव से शहर आया था मैं, जेब में सिर्फ़ दो हज़ार रुपये थे। "
    "पहले साल बहुत मुश्किल थी, लेकिन मैंने हार नहीं मानी। ग्राहक का भरोसा ही "
    "असली कमाई है, यह बात हर नौजवान को समझनी चाहिए। अगर आप भी अपना काम शुरू "
    "करना चाहते हैं तो पहले छोटा सोचो, फिर बड़ा करो। मेहनत और ईमानदारी — "
    "बस यही दो चीज़ें काम आती हैं।"
)


class TestWidenedEquivalence:
    @pytest.mark.asyncio
    async def test_head_equal_sample_gaming_rescue_unchanged(self, monkeypatch):
        """Passing a guardrail sample identical to the head sample
        reproduces today's behaviour exactly (the gaming rescue from
        the guardrails suite, with the new argument supplied)."""
        _install_all_fail(monkeypatch)
        result = await classify_niche(
            GAMING_TRANSCRIPT, [], title=GAMING_TITLE,
            guardrail_sample=GAMING_TRANSCRIPT,
        )
        assert result["niche"] == "gaming"
        assert result["is_fallback"] is True
        assert result["guardrail_rescued"] is True

    @pytest.mark.asyncio
    async def test_short_hindi_transcript_unchanged(self, monkeypatch):
        """Short transcripts pass through the builder untouched, so
        the Hindi language rescue behaves exactly as before."""
        assert _widen(HINDI_BUSINESS_TRANSCRIPT) == HINDI_BUSINESS_TRANSCRIPT
        _install_all_fail(monkeypatch)
        result = await classify_niche(
            HINDI_BUSINESS_TRANSCRIPT, [], title=HINDI_BUSINESS_TITLE,
            guardrail_sample=_widen(HINDI_BUSINESS_TRANSCRIPT),
        )
        assert result["niche"] == "hindi_podcast"
        assert result["is_fallback"] is True
        assert result["guardrail_rescued"] is True
