# Sentient Voice Recognition

A separate Home Assistant OS integration that estimates who spoke an Assist command. The Voice Recognition STT adapter passes the original microphone stream to your existing speech-to-text provider while collecting a short copy in memory. The local Sentient Voice Engine compares that audio to enrolled voice profiles. The Voice Recognition conversation adapter passes the original request to Sentient, along with the likely display name if you enable that option.

Your Assist pipeline still chooses and runs its own TTS. You can use Home Assistant Cloud TTS, Piper, or any other TTS provider your Assist pipeline supports. The integration works on Assist microphone turns routed through both adapters, such as your ESPHome living room and kitchen speakers. Use a long phrase when testing. Brief commands and uncertain matches can return **unknown**.

## Install on Home Assistant OS

This build targets Home Assistant Core **2026.11.0** and the Home Assistant OS Supervisor. The companion app runs the voice models in a container with the libraries they need.

1. Add [this GitHub repository](https://github.com/conahmorgan2304/sentient-voice-recognition) in **Settings → Apps → Install app → Repositories**. Copy the repository URL exactly as shown.
2. Open **Sentient Voice Engine** in the app store and select **Install**. Home Assistant builds the app and verifies the downloaded model checksums.
3. Open the app’s **Configuration** tab. Set `token` to a private random value of 24–128 letters, digits, hyphens or underscores. For example, generate your own with `openssl rand -hex 24`. Save the setting and start the app.
4. In **HACS → Custom repositories**, add [the same URL](https://github.com/conahmorgan2304/sentient-voice-recognition) with category **Integration**. Download **Sentient Voice Recognition** and restart Home Assistant.
5. Open **Settings → Devices & services → Add integration → Sentient Voice Recognition**. Choose **GitHub app repository** and enter the same token. Select your current speech-to-text provider and your current **Sentient** or **Jarvis with backup** conversation agent. Enable **Let Sentient identify speakers locally** if you want to ask who is speaking.
6. Create a test voice assistant under **Settings → Voice assistants** and assign it to one ESPHome speaker. Use your existing wake word and the settings below.

| Assist setting | Value |
| --- | --- |
| Speech-to-text | **Sentient Voice Recognition STT** |
| Conversation agent | **Sentient Voice Recognition** |
| Text-to-speech | The TTS you already use, with its current voice |
| Wake word | Your current wake word, such as **Hey Jarvis** |
| Prefer handling commands locally | Off on the test pipeline, so commands reach the Voice Recognition conversation adapter |

If you want to revert the test, assign the original Assist pipeline back to the speaker. You can remove Voice Recognition after moving all devices off its adapters.

## Manual installation and upgrades

For a manual installation, copy `addons/sentient_voice_engine` into the Home Assistant `local_apps` share. Install it from **Local apps** in the app store. Copy `custom_components/sentient_voice_recognition` into the Home Assistant configuration directory and restart. Select **Local app folder** when adding the integration.

Existing 0.1.0 and 0.2.0 installations load their profiles with the same speaker IDs. If you switch from the local app to the repository app, configure and start the repository app first, then choose **GitHub app repository** under the integration’s **Voice pipeline and matching settings**.

## Set up one profile per person

Open **Settings → Devices & services → Sentient Voice Recognition → Configure → Voice profiles**.

- Choose **Record with microphone**, then **Open the microphone recorder**. Enter a name, record each line, and listen back or retake as needed. Confirm consent and select **Save voice profile**. The recorder uses the microphone of the phone or computer running Home Assistant. It requires an HTTPS connection and browser microphone permission. If the app’s browser cannot access the microphone, open your HTTPS Home Assistant address in a browser.
- Choose **Upload three WAV files** to use existing recordings.
- Choose **Record from a speaker**, select the Assist satellite, enter the name and confirm consent. Say its wake word and read the numbered phrases in order. If the satellite stops listening between phrases, say its wake word again. The window closes after 10 minutes. An existing name replaces that profile’s samples. The selected pipeline’s speech-to-text provider receives the audio for transcription, and Voice Engine builds the profile locally. Use the browser recorder or WAV upload for enrolment without pipeline transcription.

The recorder checks for silent or very quiet takes before offering playback. It keeps usable takes in the browser until you save. Home Assistant sends them to the local Voice Engine for processing and stores the derived profile. Repeat for each consenting household member. Use three different sentences and aim for **4–8 seconds** of clear speech in each recording. The upload form accepts 16-bit PCM WAV, mono or stereo, at 8–48 kHz, up to 20 seconds per file.

**Suggested read-aloud script**

1. “Could you turn on the living room lamp and tell me what the weather will be like tomorrow?”
2. “I'd like to know how warm it is upstairs before I open the bedroom window for a while.”
3. “Please check whether the back door is closed, then tell me if any lights are still on downstairs.”

The prompts appear in the microphone recorder and WAV upload form. Keep the TV off and have one person speak per take. If a sample cannot be used, the form can point to the recording that needs another take.

If your recorder produces an M4A file, this converts it to the format the form expects.

```sh
ffmpeg -i sample.m4a -ac 1 -ar 16000 -c:a pcm_s16le sample.wav
```

Enrollment saves a voice template locally. Home Assistant deletes the processed upload files. Use **Rename a profile** to change the display name, **Replace a person's samples** to refresh a template while keeping its speaker ID, or **Delete a profile** to remove it. Enrollment checks for inconsistent samples and obvious duplicates across profiles. Each profile is a voice estimate for personalization, separate from a Home Assistant account. Home Assistant backups taken before deletion can still contain the profile. This feature is for conversational personalization. Home Assistant permissions and Sentient's command confirmations continue to use their existing rules.

## Test a WAV before switching a speaker

Under **Configure → Voice profiles → Test a recording**, upload a fresh sample from one enrolled person. Pick the expected profile to see a pass or fail, or choose **Show the result only**. Then try a fresh sample from a consenting, unenrolled person with **Unknown speaker** as the expected result. The report shows the result, best similarity and, when at least two profiles are enrolled, the lead over the next closest profile.

A WAV test uses the same matcher as Assist and keeps the result inside the settings dialog. Use the live test below to check your ESPHome microphones, room noise and wake word.

## Test a live Assist speaker

1. In **Developer tools → Events**, listen for `sentient_voice_recognition_turn`.
2. Have an enrolled person ask a longer question using the test speaker. The event shows the speaker name and similarity when matched.
3. Ask an unenrolled person to use the same speaker. Check that the result returns a null speaker and that Sentient still answers.
4. Try a short command and then a longer question. Each utterance is assessed independently. A short turn can be unknown even if the previous turn matched.
5. Stop the Voice Engine app briefly and speak again. The pipeline should keep transcribing and answering while the event reports that the engine is unavailable. Restart the app after the check.

The event contains `speaker_id`, `speaker_name`, `similarity`, `runner_up_gap`, `reason`, `device_id`, and `satellite_id` when available. The similarity is a model score. The defaults require at least **0.65** similarity and a **0.10** lead over the next enrolled profile. Raise the threshold in **Configure → Voice pipeline and matching settings** if another speaker is assigned the wrong name. If Home Assistant bypasses the conversation adapter through a sentence trigger or its local intent shortcut, that turn has no Voice Recognition event.

Speaker inference and voice templates stay on your Home Assistant system. Your existing cloud STT or TTS providers keep their existing data flows. When name sharing is enabled, the selected Sentient conversation agent can include the display name in a request to its configured cloud model. Name sharing is off initially.

## Ask Sentient who is speaking

Update Voice Recognition to **0.2.5** or later and [Sentient](https://github.com/conahmorgan2304/sentient) to **1.3.4** or later. In Voice Recognition, open **Configure → Voice pipeline and matching settings**, select your existing Sentient agent, and enable **Let Sentient identify speakers locally**. You can leave **Share matched names with the conversation agent** off. The local answer skips the AI model and uses your existing TTS. A cloud TTS provider receives the spoken reply, which includes the name when matched.

Ask **“Can you tell me who you think is speaking to you right now?”** in English. The longer phrase gives Voice Recognition more speech to analyse. Sentient reports the current match or says it couldn't match your voice. In guest mode, Sentient keeps the name out of this reply. Turning the local setting off stops the local handoff and keeps the saved profiles.

Voice Recognition supplies identity for a single turn. Sentient's own conversation history and account identity continue to work through their existing Home Assistant session rules. Use one speaker per utterance when testing.

[Home Assistant local app installation guide](https://contributors.home-assistant.io/apps/first-app/)  
[Home Assistant Assist pipeline documentation](https://contributors.home-assistant.io/core/internals/assist-pipelines/)  
[Sherpa-ONNX speaker identification models](https://k2-fsa.github.io/sherpa/onnx/speaker-identification/)  
[3D-Speaker model project](https://github.com/modelscope/3D-Speaker)

## Development

Run the tests with `pip install -r requirements_test.txt && pytest -q`. They cover the engine endpoint (authentication, size limits, error mapping) and the per-turn speaker lookup, without needing the voice models. The Validate workflow also runs hassfest and HACS checks.

Licence: MIT.
