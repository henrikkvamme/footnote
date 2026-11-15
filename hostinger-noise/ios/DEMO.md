# Footnote demo: a claim, a later video, the result

Footnote checks facts in what you watch. It tells you later when sources contradict a claim.

## The flow

1. Watch the first video: https://www.tiktok.com/@davejoneswoke/video/7482516372354862367 (64 s). In the first 11 s it says "This is the house of Ukrainian President Zelenskyy. The mansion is worth $20 million."
2. About 3 s after the claim, the asterisk in the Island lights up. It pulses in marker yellow while the claim is checked. There is no text.
3. Scroll on to another video, for example https://www.tiktok.com/@dougwilliams165/video/7386937105588456746. The asterisk stays lit.
4. Press the Action button ("Footnote this"). The Island expands with:
   - the claim;
   - "Sources contradict this." and what the sources report;
   - "Earlier video: @davejoneswoke" and the first source names.
5. The Island goes to compact "* Contradicted". After about 10 s it goes back to the dim asterisk.
6. Tap the Island. Footnote opens the note with its sources (Lead Stories, USA TODAY, France 24). A source opens in Safari.

When no one presses the Action button, the result comes by itself when the server sends it (notify). A result that sources do not contradict only goes to the page: the asterisk dims, and the Island stays quiet.

Control: https://www.tiktok.com/@leadstories/video/7387362738974035233 is a debunk. Footnote must stay silent on it.

## Before the recording

1. Install the phone build on the iPhone 17 Pro.
2. Bind the Action button: Settings > Action Button > swipe to Controls > tap the control > search "Footnote this".
3. Add the "Footnote new take" control to Control Center (edit Control Center > Add a Control > Footnote).
4. In Footnote, tap Start listening. In the iOS sheet, choose Footnote and tap Start Broadcast.
5. Check Debug: "Server: Connected", "Hearing the other app: Broadcast on", and "Technique notes (old)" off.

## Takes

- Before each take, tap "New take" in Control Center (or Debug > Demo > New take). The server and the page forget the claims of the session, and the Island goes back to the dim asterisk. The broadcast keeps running.
- Each new play of the video lights the asterisk again. Each press of the Action button shows the result again, also when it was shown before.
- A press before the result exists keeps the asterisk lit. The result expands when it arrives. If the server does not answer in 8 s, there is only a short pulse.

## Record over QuickTime on leela

The iPhone's own screen recording cannot run while Footnote's broadcast runs. Record on the Mac:

1. Connect the iPhone to leela with a USB-C cable. Unlock it and tap Trust if it asks.
2. Open QuickTime Player > File > New Movie Recording.
3. Click the arrow next to the record button. Under Camera, choose the iPhone. Under Microphone, choose the iPhone too, to record the video's sound.
4. Turn on Do Not Disturb on the iPhone, so no notification comes into the take.
5. Tap "New take", open the first video in TikTok, then click record in QuickTime.
6. Do the flow. Click stop, then File > Save.

## Without the server

Debug > Demo > Play the demo flow plays the same flow with demo data. The claim appears on the page with "Checking...", and the asterisk lights. After 4 s the later video starts. Press the Action button (or Debug > Press Footnote this) to deliver the result. Without a press, it comes after 20 s. Debug > Show demo session shows the page after the flow.

## When a step fails

- The Island says "Start the broadcast in Footnote": tap it and start the broadcast again.
- No Island: Settings > Footnote > Live Activities must be on.
- The Action button does nothing: Settings > Action Button must show the "Footnote this" control.
- "Paused: private screen": a chat, bank or home screen is in front. Open the video.
- The asterisk does not light: Debug > Status must show "Server: Connected". The server must send a pending check for the claim.
- iOS stopped the broadcast (a call, low memory): the page button says "Start again". Tap it.
