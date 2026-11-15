"""Writes set.jsonl: the labelled transcript sentences of the gate eval.

The sentences are hand written short-video speech. `label` is `check` when the speaker asserts a checkable factual claim
of public interest in their own voice (also with a vague source such as "people say"), else `skip`.
The split is fixed: in each category, items 3, 6 and 9 of each run of 10 go to test (30%), the rest to dev.

    python3 make_set.py   # rewrites set.jsonl
"""

import json
import os

C, S = "check", "skip"

# (category, label, text, context or None)
ROWS = []


def add(cat, label, items):
    for it in items:
        text, ctx = (it, None) if isinstance(it, str) else it
        ROWS.append((cat, label, text, ctx))


add("asserted_false", C, [
    "the WHO just admitted that the covid vaccine changes your DNA, it's in their own report",
    "So Finland literally banned cash last month, you can't pay with coins anywhere anymore",
    "the EU is going to make it illegal to grow your own vegetables starting January",
    "Bill Gates bought up 80 percent of all farmland in the US, that's why food is so expensive",
    "5G towers were switched on in Wuhan two weeks before the outbreak started",
    "Canada passed a law that lets the government take your kids if you don't use their pronouns",
    "Ukraine sold the Javelins we gave them on the dark web, there are listings with serial numbers",
    "the Pentagon confirmed that the Maui fires were started by a directed energy weapon",
    "Sweden has the highest rape rate in the world now because of immigration",
    "inflation in the UK hit 25 percent this year, the highest since the war",
    "Germany shut down all of its hospitals' gas heating to send gas to Ukraine",
    "the new digital euro will expire if you don't spend it within 90 days, the ECB said so",
    "Zelensky bought a 75 million dollar villa in Egypt with aid money",
    "France just raised the retirement age to 70, Macron signed it on Tuesday",
    "Over 40,000 people died from the vaccine in Norway alone, the health institute hid the numbers",
    "Taylor Swift was paid 10 million dollars by the Pentagon to tell people to vote",
    "the Amazon rainforest has actually grown by 30 percent since 2010",
    "Japan stopped vaccinating children completely in 2023 after the deaths",
    "Pfizer's CEO was arrested in Belgium last week for fraud",
    "the US printed 80 percent of all dollars that exist in just the last two years",
    "Norway is going to ban petrol cars from the roads entirely next year, not just new sales",
    "Russia lost one point five million soldiers just in the last month",
    "the UN is planning to put a tax on every cow and pig in Europe from 2027",
    "China owns the Port of Rotterdam now, they bought it in secret",
    "Greta Thunberg's family owns a big oil company in Sweden",
    "the voting machines in Georgia switched 3 million votes, a judge ruled that last week",
    "drinking a teaspoon of baking soda a day cures cancer, oncologists don't want you to know",
    "The ECB just announced it will freeze all savings accounts over 100,000 euros",
    "the moon landing footage was shot in a studio in Nevada, NASA admitted it in 2019",
    "Ireland gives every asylum seeker a free house and 2,000 euros a month",
    "the Bank of England stopped printing paper notes completely this summer",
    "Microsoft cancelled Windows for all of Russia and turned off every computer there",
    "the Gulf Stream already stopped in March, scientists confirmed it",
    "they found that wind turbines killed more birds last year than cats did, that's the official figure",
    "Denmark now pays people 1,000 euros to give up their car",
    "the new EU law means your car will be remotely switched off if you drive more than 100 kilometres a week",
    "Elon Musk bought CNN for 30 billion dollars",
    "the Supreme Court ruled last week that income tax is unconstitutional",
    "Every single member of the Finnish parliament got a 40 percent pay rise this year",
    "the Netherlands closed half of its farms by law in 2024",
])

add("asserted_true", C, [
    "Finland joined NATO in April 2023, that's when it became the 31st member",
    "the ECB raised interest rates ten times in a row between 2022 and 2023",
    "Russia launched its full scale invasion of Ukraine on February 24th 2022",
    "the UK left the European Union officially on January 31st 2020",
    "Over 6 million Ukrainians have registered as refugees in Europe",
    "Norway's oil fund is worth more than 1.5 trillion dollars now",
    "the US Supreme Court overturned Roe v Wade in June 2022",
    "Sweden became NATO's 32nd member in March 2024",
    "Inflation in the euro area peaked at over 10 percent in October 2022",
    "the Paris agreement aims to keep warming well below 2 degrees",
    "Germany shut down its last three nuclear plants in April 2023",
    "the measles vaccine prevented an estimated 57 million deaths since 2000",
    "so Croatia adopted the euro on January 1st 2023",
    "the EU AI Act was approved by the European Parliament in March 2024",
    "Joe Biden dropped out of the presidential race in July 2024",
    "the minimum wage in Germany went up to 12 euros 41 in 2024",
    "Hungary's Viktor Orban has been prime minister since 2010",
    "the Kakhovka dam was destroyed in June 2023 and flooded huge areas",
    "Tobacco kills more than 8 million people every year",
    "Iceland had a volcanic eruption near Grindavik that forced the whole town to evacuate",
    "the Bank of Japan ended negative interest rates in March 2024, the first hike in 17 years",
    "the Danish parliament voted to abolish a public holiday, Great Prayer Day, to fund defence",
    "Estonia was the first country to allow online voting in national elections",
    "Around 700 million people live in extreme poverty right now",
    "Spain's government approved a four and a half day work week trial for companies",
    "2023 was the hottest year on record for the whole planet",
    "Argentina's inflation went above 200 percent in 2023",
    "France was the first country to put abortion rights in its constitution, in March 2024",
    "Norway gives free university tuition even to students from outside the EU at public universities, wait no they stopped that in 2023",
    "the Finnish government cut the adult education allowance completely from 2024",
    "Meta was fined 1.2 billion euros by the Irish data regulator in 2023",
    "Lithuania stopped importing Russian gas in 2022, the first EU country to do it",
    "Twitter was bought by Elon Musk for 44 billion dollars in 2022",
    "the EU agreed to ban the sale of new petrol and diesel cars from 2035",
    "UK prime minister Liz Truss resigned after just 45 days",
])

add("vague_source_assert", C, [
    "people are saying the government is quietly adding fluoride to bottled water now, and it's true",
    "experts say one in three young people in Finland can't afford rent anymore",
    "my sources tell me the army is calling up 50,000 reservists next month",
    "apparently the new law makes it a crime to post memes about politicians in the UK, yeah that's real",
    "everyone knows the EU paid 2 billion euros for vaccines that were never delivered",
    "a doctor friend told me hospitals get paid 39,000 dollars for every covid patient on a ventilator, which is true",
    "studies show that 70 percent of food in supermarkets has microplastics",
    "insiders say Tesla is closing its Berlin factory next year",
    "I read that Norway is giving each citizen 10,000 kroner because of the oil fund, it's happening",
    "word is the fuel tax goes up by 20 cents a litre in January",
])

add("opinion", S, [
    "honestly I think this government is the worst we've ever had",
    "the EU is just a bunch of bureaucrats who don't care about normal people",
    "I really believe nuclear power is the future, I don't get why people are scared of it",
    "Immigration has been a total disaster and nobody wants to admit it",
    "Biden was way too old to run, that's just my opinion",
    "I think raising taxes on the rich is completely fair",
    "climate activists gluing themselves to roads is so counterproductive",
    "the media is so biased it's actually insane",
    "Putin is a war criminal, period",
    "I feel like nobody in politics actually listens to young people",
    "electric cars are honestly overrated",
    "the police in this country are doing a great job and nobody says thanks",
    "Universal basic income would fix so much, I'm convinced",
    "I just think the monarchy is outdated",
    "sanctions clearly aren't working, they hurt us more than them",
    "I trust doctors more than random people on the internet, sorry not sorry",
    "the housing market is broken, like completely broken",
    "voting is pointless if both parties are the same",
    "this new prime minister seems way more competent to be honest",
    "the school system is failing our kids and it makes me so angry",
    "nato expansion was a mistake in my view",
    "Social media should be banned for under 16s, I'd vote for that tomorrow",
    "Tax money should go to healthcare not weapons",
    "things are getting worse every year",
    "nobody really knows what's going on in that war",
])

add("prediction", S, [
    "mark my words, the euro will collapse within five years",
    "I bet the government falls before Christmas",
    "inflation is going to go crazy again next year",
    "Trump is going to win again, watch",
    "petrol will be 3 euros a litre by summer, guaranteed",
    "this war will be over by spring I think",
    "house prices are about to crash hard",
    "AI is going to take half of all jobs in ten years",
    "they will bring back lockdowns this winter, just wait",
    "China will invade Taiwan by 2027",
])

add("quoted_reported", S, [
    "The finance ministry said on Monday that the deficit will reach 4 percent this year",
    "According to Reuters, the talks in Geneva ended without an agreement",
    "the prime minister told reporters that taxes would not go up",
    "Russia's defence ministry claims it shot down 30 drones over Belgorod",
    "the police say two people were arrested after the protest",
    "the WHO stated that the risk to the general public remains low",
    "Kremlin spokesman Peskov said the talks were not on the agenda",
    "The BBC reports that the strike will continue into next week",
    "the company said in a statement that no customer data was leaked",
    "Ukraine's general staff reported 1,200 Russian casualties in the last day, that's their figure",
    "the governor said all schools will be closed tomorrow because of the storm",
    "according to the IMF the global economy will grow 3 percent this year",
    "the opposition leader claimed the election was rigged, the electoral commission rejected that",
    "The minister of health announced a new vaccine campaign for the autumn",
    "the US State Department said it was deeply concerned about the arrests",
    "and the European Commission says it will propose new rules in March",
    "they say that the new tax will only hit people earning over 100k",
    "the Chinese foreign ministry said the reports were groundless",
    "Statistics Finland reported unemployment at 8 point 5 percent in October",
    "the union says 90 percent of workers voted for the strike",
    "the court said it would announce its ruling next month",
    "NATO's secretary general said member states must spend more on defence",
    "the White House press secretary said the president was not briefed",
    "officials say the fire is now under control",
    "the airline claims the delays were caused by air traffic control",
])

add("debunk", S, [
    ("the WHO admitted that the covid vaccine changes your DNA", "Okay this video has 3 million views and it's completely false. It says"),
    ("Finland banned cash last month", "Fact check time. There's a viral clip claiming that"),
    ("No, the EU is not making it illegal to grow your own vegetables, that's a total myth", None),
    ("they're saying Bill Gates owns most of the farmland in America", "Let's debunk this one."),
    ("5G caused covid", "I keep seeing this claim, and I want to be really clear it's nonsense:"),
    ("so this guy says Ukraine is selling Javelins on the dark web, which multiple investigations found zero evidence of", None),
    ("the Maui fires were started by a laser weapon from space", "This is the dumbest conspiracy theory of the year. People actually believe"),
    ("that claim that the digital euro expires after 90 days is fake, the ECB never said anything like that", None),
    ("Zelensky bought a villa in Egypt", "A fake news site made up a story, and now millions think"),
    ("it says 40,000 people died from the vaccine in Norway", "This screenshot is fabricated. Look at it,"),
    ("the voting machines in Georgia switched millions of votes", "Every court threw this out. The claim was that"),
    ("baking soda does not cure cancer, please don't believe these videos", None),
    ("the moon landing was filmed in Nevada", "Myth number three, and this one won't die:"),
    ("the claim is that Ireland gives every asylum seeker a free house, and that is simply not true", None),
    ("the Gulf Stream already stopped", "No, scientists did not confirm that"),
    ("Pfizer's CEO was arrested in Belgium", "Reuters checked this and found no record that"),
    ("Taylor Swift is a Pentagon psyop paid to make people vote", "Conspiracy theorists are claiming, again with no evidence, that"),
    ("the Amazon has grown by 30 percent", "This chart is going viral but it's misleading, it claims"),
    ("Sweden has the highest rape rate in the world", "Let me explain why this statistic is wrong. People share that"),
    ("the Supreme Court ruled income tax is unconstitutional", "That's a satire site, guys. It wasn't real. The headline said"),
    ("so the post says Germany shut off hospital heating to send gas to Ukraine. That never happened", None),
    ("Elon Musk did not buy CNN, that was a parody account", None),
    ("they said Denmark pays you 1,000 euros to give up your car", "Okay I looked into this and it's false."),
    ("China owns the Port of Rotterdam", "Debunking viral claims part 12. Claim:"),
    ("Microsoft turned off every computer in Russia", "This is misinformation and here's why. The video says"),
])

add("question", S, [
    "did Finland really ban cash or is that fake?",
    "wait is it true that the EU is banning home gardens?",
    "so why is nobody talking about the inflation numbers?",
    "Has anyone actually checked whether the vaccine changes your DNA?",
    "what if the moon landing was actually staged?",
    "how is it legal for the government to do this?",
    "Is Sweden really the most dangerous country in Europe now?",
    "who actually owns the farmland in America?",
    "can someone explain why petrol costs 2 euros now?",
    "is Zelensky actually buying villas with our money or is that propaganda?",
    "why did the ECB raise rates again?",
    "did Biden really drop out or am I confused?",
    "So what happens to my savings if the bank goes bust?",
    "does anyone know if the new tax law starts in January?",
    "Why would they close the hospitals in winter?",
    "are wind turbines actually killing all the birds?",
    "where does all the aid money go?",
    "is the Gulf Stream really stopping?",
    "what do you guys think about the new prime minister?",
    "who voted for this?",
])

add("cooking_lifestyle", S, [
    "okay so you add two cups of flour and a pinch of salt and mix it really well",
    "this cast iron pan holds heat three times longer than a nonstick one",
    "Bake it at 180 degrees for 25 minutes until it's golden",
    "honey never spoils, they found 3,000 year old honey in Egyptian tombs that was still edible",
    "this moisturiser has 5 percent niacinamide which is perfect for oily skin",
    "Avocados have more potassium than bananas, so eat your avocado toast",
    "my morning routine starts with 500 ml of water and ten minutes of stretching",
    "IKEA's Billy bookcase has sold over 60 million units worldwide",
    "this air fryer uses 70 percent less oil than a deep fryer",
    "the trick to fluffy rice is rinsing it three times",
    "Coffee was first discovered in Ethiopia by a goat herder, that's the story anyway",
    "this outfit is from Zara and the jacket was only 39 euros",
    "you need to let the dough rest for at least an hour",
    "Messi has won the Ballon d'Or eight times, more than anyone ever",
    "Real Madrid won the Champions League 15 times",
    "the iPhone 16 has a 48 megapixel camera and it's insane for food photos",
    "Taylor Swift's Eras tour made over 2 billion dollars",
    "Lapland has about 200,000 reindeer, we saw so many on our trip",
    "this hotel in Lisbon was only 60 euros a night with breakfast",
    "dark chocolate with 70 percent cocoa has less sugar than milk chocolate",
    "five minutes of cold shower every morning changed my life honestly",
    "add the garlic last so it doesn't burn",
    "Norway won the most medals at the winter Olympics in 2022",
    "the new Zelda game sold 10 million copies in three days",
    "this sunscreen is SPF 50 and doesn't leave a white cast",
])

add("personal_story", S, [
    "so last year I lost my job and I had to move back in with my parents",
    "my grandma lived through the war and she never talked about it",
    "I got my covid shot in 2021 and honestly I felt fine after",
    "when I moved to Helsinki I couldn't find an apartment for four months",
    "my dad was a farmer and the subsidies basically kept us alive",
    "I waited 9 hours in the emergency room last week with my son",
    "I voted for the first time yesterday and I was so nervous",
    "my rent went up by 200 euros this year and my salary didn't",
    "I was at the protest on Saturday and it was really peaceful",
    "my brother is in the army and we haven't heard from him in a week",
    "I paid off my student loan finally after 8 years",
    "when I was a kid we never had money for holidays",
    "I worked as a nurse for ten years and I burned out",
    "my electricity bill was 400 euros in January, I almost cried",
    "I got scammed by a fake bank call and lost 3,000 euros",
    "my family fled Syria in 2015 and we ended up in Germany",
    "I tried to see a doctor and the next appointment was in six weeks",
    "I grew up near the Russian border and we always felt it",
    "I lost 20 kilos after I stopped drinking soda",
    "my mom has been a teacher for 30 years and she's never been this tired",
])

add("satire", S, [
    "breaking news, the government has announced that Mondays are now illegal",
    "Scientists confirm that the economy is fine as long as you don't look at it",
    "in other news, the prime minister was replaced by a cardboard cutout and nobody noticed for three weeks",
    "The EU has officially classified bananas that are too curved as a threat to democracy",
    "and Putin just announced he's invading the moon because it looked at him funny",
    "Parliament voted 200 to 0 to give themselves a nap room, the only thing they ever agreed on",
    "the ministry of finance has solved inflation by banning prices",
    "BREAKING the Finnish government will now pay you in saunas",
    "Elon Musk bought the sun and will start charging for daylight next month",
    "experts warn that thinking too hard about politics may cause you to become a politician",
    "the central bank will now print money shaped like apologies",
    "Biden and Trump agreed to settle the election with a game of Mario Kart",
    "Officials confirm the new tax is actually just them asking nicely for your money",
    "this just in, the weather forecast is now made by a guy named Kevin looking out the window",
    "and the new defence strategy is just asking the enemy to please stop",
])

add("noisy", C, [
    "so the european central bank race interest rates again to four point five percent",
    "finland join nato in april twenty twenty three yeah",
    "the WH0 said that the vaccine change your d n a it's in there report",
    "Zelenski bought a 75 million dollar Villa in egypt with the aid money",
    "germany shut down there last three nuclear plants in april",
    "and the uk inflation hit twenty five percent this year highest since the",
    "bill gates own eighty percent of farm land in the u s that's why food",
    "macro signed the law raising the retirement age to seventy on tuesday",
    "uh so like the e u is gonna ban growing you're own vegetables from january",
    "russia launched the full scale invasion on february twenty four twenty twenty two",
    "the voting machines in georgia flipped three million votes a judge ruled it",
    "sweden became the thirty second member of nato in march",
    "Over forty thousand people died from the vaccine in norway they hid the numbers",
    "argentina inflation went above two hundred percent last year",
    "the eecb will freeze all savings over hundred thousand euro",
    "the dam in kakovka was blown up in june and flooded the whole",
    "orban has been prime minister since twenty ten in hungary",
    "they printed eighty percent of all dollars in the last two years the fed",
    "the gulf stream already stop in march scientist confirm",
    "biden drop out of the race in july twenty twenty four",
    "liz trust resigned after forty five days",
    "pfizer ceo was arrested in belgium last week for for fraud",
    "croatia adopted the euro on the first of january twenty three",
    "denmark pay people thousand euros to give up there car",
    "japan stop vaccinating kids completely in twenty twenty three after the deaths",
])

add("noisy", S, [
    "okay so you add the uh the flour and then mix it",
    "i think the government is like the worst ever honestly",
    "did finland really ban cash or",
    "the ministry said that the deficit will be four per cent this",
    "my rent went up two hundred euros and my",
    "messi won the ballon d or eight times more than",
    "fact check this video says the wh o admitted the vaccine change dna that's false",
    "mark my words euro collapse in five years",
    "so yeah um like subscribe for more",
    "bake at one eighty for twenty five minutes",
    "breaking news mondays are now illegal",
    "the police say two people were arrested after the",
    "why is nobody talking about this",
    "and then my grandma she she lived through the war",
    "this is so crazy you guys",
])

SPLIT_TEST = {2, 5, 8}  # 0-based positions in each run of 10 within a category: 30% test


def main():
    seen, count, out = set(), {}, []
    for cat, label, text, ctx in ROWS:
        assert text not in seen, text
        seen.add(text)
        i = count.get(cat, 0)
        count[cat] = i + 1
        row = {"id": "%s-%03d" % (cat, i), "text": text, "label": label, "category": cat,
               "split": "test" if i % 10 in SPLIT_TEST else "dev"}
        if ctx:
            row["context"] = ctx
        out.append(row)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "set.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for row in out:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    n_test = sum(r["split"] == "test" for r in out)
    print("%d items (%d check, %d skip), %d dev, %d test" % (len(out), sum(r["label"] == C for r in out),
                                                           sum(r["label"] == S for r in out), len(out) - n_test, n_test))


if __name__ == "__main__":
    main()
