# Strings that need a native-speaker review

**Nothing in `te.json` or `hi.json` has been checked by a Telugu or Hindi speaker.**
Every string below was written by a machine (Claude) or taken from the elder app's own
wording, and none of it should be called correct until someone who reads the language has
signed it off. The `en.json` text is the source of truth for meaning.

- "Source" says where the wording came from: `new` = written for this console, no app
  string existed; `elder-app ...` = reused or adapted from `clients/elder-app/elder_app/elder_app.py`
  (the app has English and Telugu only, **no Hindi at all**, so every Hindi string is new).
  Reused app strings are the app's own wording and are *not* verified here either.
- Several strings carry technical terms (ASR, MT, Render, ms, `start_demo.py`, `--asr-en off`)
  that were left in English on purpose; a reviewer may want a different choice.
- A test (`tests/test_multilingual.py`) fails if a key in `te.json` / `hi.json` is missing from
  this table, so a new string cannot slip in unlisted.
- Server error messages that the page cannot map to a key (rare) are shown in English.

| Key | Language | English source | Current text | Source |
|---|---|---|---|---|
| `page_title` | Telugu | SatSandesh voice demo | సత్‌సందేశ్ వాయిస్ డెమో | new |
| `heading` | Telugu | SatSandesh voice demo | సత్‌సందేశ్ వాయిస్ డెమో | new |
| `subheading` | Telugu | Speak a note in your language, read it in English, then send a reply and hear it spoken. | మీ భాషలో ఒక సందేశం మాట్లాడండి, ఇంగ్లీష్‌లో చదవండి, ఆపై జవాబు పంపి దాన్ని వినండి. | new |
| `page_lang_label` | Telugu | Page language | పేజీ భాష | new |
| `page_lang_aria` | Telugu | Choose the page language | పేజీ భాషను ఎంచుకోండి | new |
| `dep_asr` | Telugu | Telugu/Hindi speech | తెలుగు/హిందీ ప్రసంగం | new |
| `dep_asr_en` | Telugu | English speech | ఇంగ్లీష్ ప్రసంగం | new |
| `dep_mt` | Telugu | Translation | అనువాదం | new |
| `dep_render` | Telugu | Reply voice | జవాబు స్వరం | new |
| `state_checking` | Telugu | checking… | తనిఖీ చేస్తోంది… | new |
| `state_ready` | Telugu | ready | సిద్ధం | new |
| `state_warming` | Telugu | warming up… | సిద్ధమవుతోంది… | new |
| `state_down` | Telugu | not running | నడవడం లేదు | new |
| `state_off` | Telugu | turned off | ఆపివేయబడింది | new |
| `state_unknown` | Telugu | not responding | స్పందించడం లేదు | new |
| `host_banner` | Telugu | The microphone only works when this page is opened as http://127.0.0.1:{port}/ — not through a LAN IP or computer name. Typing text still works. | ఈ పేజీని http://127.0.0.1:{port}/ గా తెరిచినప్పుడు మాత్రమే మైక్రోఫోన్ పనిచేస్తుంది — LAN IP లేదా కంప్యూటర్ పేరుతో కాదు. టెక్స్ట్ టైప్ చేయడం ఇప్పటికీ పనిచేస్తుంది. | new |
| `speak_title` | Telugu | 1. Speak a note | 1. సందేశం మాట్లాడండి | new |
| `speaker_lang_label` | Telugu | Language you will speak | మీరు మాట్లాడే భాష | new |
| `lang_en` | Telugu | English | ఇంగ్లీష్ | elder-app `lang_option_en` |
| `lang_te` | Telugu | Telugu | తెలుగు | elder-app `lang_option_te` |
| `lang_hi` | Telugu | Hindi | హిందీ | elder-app `lang_option_hi` |
| `mic_start` | Telugu | Start recording | రికార్డింగ్ ప్రారంభించండి | new |
| `mic_stop` | Telugu | Stop recording | రికార్డింగ్ ఆపండి | new |
| `status_ready` | Telugu | Ready. Press Start recording, speak one sentence, then press Stop. | సిద్ధం. “రికార్డింగ్ ప్రారంభించండి” నొక్కండి, ఒక వాక్యం చెప్పండి, తర్వాత “ఆపండి” నొక్కండి. | new |
| `status_recording` | Telugu | Recording — speak now, then press Stop. | రికార్డ్ అవుతోంది — ఇప్పుడు మాట్లాడండి, తర్వాత ఆపండి నొక్కండి. | new |
| `status_recording_server` | Telugu | Recording on the console PC — speak now… {left}s left | కన్సోల్ PC లో రికార్డ్ అవుతోంది — ఇప్పుడు మాట్లాడండి… {left} సె మిగిలింది | new |
| `status_elapsed` | Telugu | {what} {elapsed}s | {what} {elapsed} సె | new |
| `status_done` | Telugu | Done. Press Start recording to try another note. | పూర్తయింది. మరో సందేశం కోసం రికార్డింగ్ ప్రారంభించండి నొక్కండి. | new |
| `status_done_typed` | Telugu | Done. Type another sentence, or press Start recording. | పూర్తయింది. మరో వాక్యం టైప్ చేయండి, లేదా రికార్డింగ్ ప్రారంభించండి నొక్కండి. | new |
| `status_stopped` | Telugu | Stopped at the “{step}” step. | “{step}” దశలో ఆగిపోయింది. | new |
| `run_record` | Telugu | Recording — speak now… | రికార్డ్ అవుతోంది — ఇప్పుడు మాట్లాడండి… | new |
| `run_upload` | Telugu | Sending the recording… | రికార్డింగ్ పంపుతోంది… | new |
| `run_transcribe` | Telugu | Listening to the recording… | రికార్డింగ్ వింటోంది… | new |
| `run_translate` | Telugu | Translating to English… | ఇంగ్లీష్‌లోకి అనువదిస్తోంది… | new |
| `step_record` | Telugu | Record | రికార్డ్ | new |
| `step_upload` | Telugu | Upload | పంపడం | new |
| `step_transcribe` | Telugu | Transcribe | అక్షరరూపం | new |
| `step_translate` | Telugu | Translate | అనువాదం | new |
| `step_render` | Telugu | Spoken reply | మాట్లాడే జవాబు | new |
| `step_skipped` | Telugu | skipped | వదిలేశాం | new |
| `meter_label` | Telugu | input level | ధ్వని స్థాయి | new |
| `hint_no_sound` | Telugu | No sound is reaching the microphone — check it is not muted. | మైక్రోఫోన్‌కు శబ్దం అందడం లేదు — అది మ్యూట్‌లో లేదని చూడండి. | new |
| `hint_auto_stop` | Telugu | Stopped automatically at {seconds} s. | {seconds} సె వద్ద దానంతటదే ఆగింది. | new |
| `or_type` | Telugu | or type text directly | లేదా నేరుగా టైప్ చేయండి | new |
| `type_placeholder` | Telugu | Type or paste a sentence in the language selected above… | పైన ఎంచుకున్న భాషలో ఒక వాక్యం టైప్ చేయండి లేదా అతికించండి… | new |
| `type_aria` | Telugu | Text of the note | సందేశ పాఠ్యం | new |
| `type_button` | Telugu | Use this text | ఈ పాఠ్యాన్ని వాడండి | new |
| `advanced_summary` | Telugu | Advanced: record on the console PC's own microphone | అధునాతనం: కన్సోల్ PC యొక్క సొంత మైక్రోఫోన్‌లో రికార్డ్ చేయండి | new |
| `record_button` | Telugu | Record ({seconds}s) | రికార్డ్ ({seconds} సె) | new |
| `advanced_note` | Telugu | Server-side recording; the browser microphone above is the normal path. | సర్వర్ వైపు రికార్డింగ్; సాధారణంగా పైన ఉన్న బ్రౌజర్ మైక్రోఫోన్ వాడతారు. | new |
| `conv_title` | Telugu | 2. The conversation | 2. సంభాషణ | new |
| `note_empty` | Telugu | Nothing yet. Record or type a note. | ఇంకా ఏమీ లేదు. సందేశం రికార్డ్ చేయండి లేదా టైప్ చేయండి. | new |
| `reply_empty` | Telugu | The reply appears here. | జవాబు ఇక్కడ కనిపిస్తుంది. | new |
| `note_working` | Telugu | Working on it… | పని జరుగుతోంది… | new |
| `note_who` | Telugu | Voice note | వాయిస్ సందేశం | new |
| `typed_tag` | Telugu | typed text | టైప్ చేసిన పాఠ్యం | new |
| `show_english` | Telugu | Show English | ఇంగ్లీష్ చూపించు | elder-app `recv_show_english` |
| `hide_english` | Telugu | Hide English | ఇంగ్లీష్ దాచు | new |
| `english_label` | Telugu | English | ఇంగ్లీష్ | new |
| `note_is_english` | Telugu | This note is already in English. | ఈ సందేశం ఇప్పటికే ఇంగ్లీష్‌లో ఉంది. | new |
| `produced_by` | Telugu | Written by {model} | {model} రాసింది | new |
| `reply_title` | Telugu | 3. Family reply | 3. కుటుంబ జవాబు | new |
| `reply_hint` | Telugu | Write the reply in English. It is translated and spoken in the language chosen below. | జవాబును ఇంగ్లీష్‌లో రాయండి. అది అనువదించబడి, కింద ఎంచుకున్న భాషలో వినిపిస్తుంది. | new |
| `reply_placeholder` | Telugu | English reply to translate and speak… | అనువదించి వినిపించాల్సిన ఇంగ్లీష్ జవాబు… | new |
| `reply_aria` | Telugu | English reply | ఇంగ్లీష్ జవాబు | new |
| `reply_speak_in` | Telugu | Speak the reply in | జవాబు ఏ భాషలో వినిపించాలి | new |
| `reply_go` | Telugu | Translate and speak | అనువదించి వినిపించు | new |
| `reply_busy` | Telugu | Translating and speaking in {lang}… {elapsed}s | {lang}లో అనువదించి వినిపిస్తోంది… {elapsed} సె | new |
| `reply_done` | Telugu | Done. | పూర్తయింది. | new |
| `reply_done_text_only` | Telugu | Done — text only this time. | పూర్తయింది — ఈసారి పాఠ్యం మాత్రమే. | new |
| `reply_stopped` | Telugu | Stopped at the spoken-reply step. | వినిపించే దశలో ఆగిపోయింది. | new |
| `reply_who` | Telugu | Reply in {lang} | {lang}లో జవాబు | new |
| `play` | Telugu | Play | ప్లే చేయి | new |
| `replay` | Telugu | Play again | మళ్ళీ ప్లే చేయి | new |
| `audio_length` | Telugu | audio length {seconds} s | ఆడియో నిడివి {seconds} సె | new |
| `audio_unavailable` | Telugu | Audio is not available for this one — showing the text only. | దీనికి ఆడియో అందుబాటులో లేదు — పాఠ్యం మాత్రమే చూపిస్తున్నాం. | new |
| `english_no_voice` | Telugu | English replies are text only: no English voice is installed. | ఇంగ్లీష్ జవాబులు పాఠ్యం మాత్రమే: ఇంగ్లీష్ స్వరం ఇన్‌స్టాల్ చేయబడలేదు. | new |
| `autoplay_blocked` | Telugu | Your browser blocked autoplay — press Play. | మీ బ్రౌజర్ ఆటోప్లేను ఆపింది — ప్లే నొక్కండి. | new |
| `audio_failed` | Telugu | Could not play the audio. Press Play to try again. | ఆడియో ప్లే కాలేదు. మళ్ళీ ప్రయత్నించడానికి ప్లే నొక్కండి. | new |
| `details_summary` | Telugu | Details (models and timings) | వివరాలు (మోడల్స్, సమయాలు) | new |
| `details_services` | Telugu | Services | సేవలు | new |
| `details_models` | Telugu | Models | మోడల్స్ | new |
| `details_latency` | Telugu | Time taken per step (measured, ms) | ప్రతి దశకు పట్టిన సమయం (కొలిచినది, ms) | new |
| `details_none` | Telugu | No run yet. | ఇంకా రన్ లేదు. | new |
| `model_asr` | Telugu | Speech model | ప్రసంగ మోడల్ | new |
| `model_mt` | Telugu | Translation model | అనువాద మోడల్ | new |
| `model_translate` | Telugu | Reply translation model | జవాబు అనువాద మోడల్ | new |
| `model_voice` | Telugu | Voice | స్వరం | new |
| `model_none` | Telugu | none (not needed) | ఏదీ లేదు (అవసరం లేదు) | new |
| `lat_record` | Telugu | Record (microphone, wall clock) | రికార్డ్ (మైక్రోఫోన్, వాస్తవ సమయం) | new |
| `lat_upload` | Telugu | Upload (browser → console, wall clock) | పంపడం (బ్రౌజర్ → కన్సోల్, వాస్తవ సమయం) | new |
| `lat_asr_decode` | Telugu | ASR — decode | ASR — డీకోడ్ | new |
| `lat_asr_inference` | Telugu | ASR — inference | ASR — ఇన్ఫరెన్స్ | new |
| `lat_asr_postprocess` | Telugu | ASR — postprocess | ASR — పోస్ట్‌ప్రాసెస్ | new |
| `lat_asr_total` | Telugu | ASR — service total | ASR — సేవ మొత్తం | new |
| `lat_asr_round` | Telugu | ASR — round trip (console → service) | ASR — రౌండ్ ట్రిప్ (కన్సోల్ → సేవ) | new |
| `lat_mt_total` | Telugu | MT — service total | MT — సేవ మొత్తం | new |
| `lat_mt_round` | Telugu | MT — round trip (console → service) | MT — రౌండ్ ట్రిప్ (కన్సోల్ → సేవ) | new |
| `lat_render_total` | Telugu | Render — service total (translate + speak) | Render — సేవ మొత్తం (అనువాదం + స్వరం) | new |
| `lat_render_round` | Telugu | Render — round trip (console → service) | Render — రౌండ్ ట్రిప్ (కన్సోల్ → సేవ) | new |
| `svc_asr` | Telugu | Speech recognition | ప్రసంగ గుర్తింపు | new |
| `svc_asr_en` | Telugu | English speech recognition | ఇంగ్లీష్ ప్రసంగ గుర్తింపు | new |
| `svc_mt` | Telugu | Translation | అనువాదం | new |
| `svc_render` | Telugu | Spoken reply | మాట్లాడే జవాబు | new |
| `try_again` | Telugu | Try again | మళ్ళీ ప్రయత్నించండి | phrase from elder-app `circle_error_generic` |
| `err_console_down_title` | Telugu | The demo console is not responding | డెమో కన్సోల్ స్పందించడం లేదు | new |
| `err_console_down_msg` | Telugu | The page could not reach its own backend. Is start_demo.py still running? Start it again if needed, then press Try again. | పేజీ తన సొంత బ్యాకెండ్‌ను చేరుకోలేకపోయింది. start_demo.py ఇంకా నడుస్తోందా? అవసరమైతే మళ్ళీ ప్రారంభించి, మళ్ళీ ప్రయత్నించండి నొక్కండి. | new |
| `err_timeout_title` | Telugu | The “{step}” step timed out | “{step}” దశ సమయం మించిపోయింది | new |
| `err_timeout_msg` | Telugu | No response in time. Check the service windows, then press Try again. | సమయానికి జవాబు రాలేదు. సేవల విండోలను చూసి, మళ్ళీ ప్రయత్నించండి నొక్కండి. | new |
| `err_mic_blocked_title` | Telugu | Microphone access is blocked | మైక్రోఫోన్ యాక్సెస్ నిరోధించబడింది | new |
| `err_mic_blocked_msg` | Telugu | Allow microphone access in the browser address bar, then press Try again. You can still type text below. | బ్రౌజర్ అడ్రస్ బార్‌లో మైక్రోఫోన్‌ను అనుమతించి, మళ్ళీ ప్రయత్నించండి నొక్కండి. కింద టెక్స్ట్ టైప్ చేయవచ్చు. | new |
| `err_no_mic_title` | Telugu | No microphone found | మైక్రోఫోన్ కనబడలేదు | new |
| `err_no_mic_msg` | Telugu | No input device was found. Plug in or enable a microphone, then press Try again. You can still type text below. | ఇన్‌పుట్ పరికరం కనబడలేదు. మైక్రోఫోన్ పెట్టి లేదా ఆన్ చేసి, మళ్ళీ ప్రయత్నించండి నొక్కండి. కింద టెక్స్ట్ టైప్ చేయవచ్చు. | new |
| `err_mic_busy_title` | Telugu | The microphone is busy | మైక్రోఫోన్ వేరే పనిలో ఉంది | new |
| `err_mic_busy_msg` | Telugu | Another app may be using the microphone. Close it, then press Try again. | వేరే యాప్ మైక్రోఫోన్‌ను వాడుతుండవచ్చు. దాన్ని మూసివేసి, మళ్ళీ ప్రయత్నించండి నొక్కండి. | new |
| `err_mic_start_title` | Telugu | Could not start the microphone | మైక్రోఫోన్ ప్రారంభించలేకపోయాం | new |
| `err_mic_start_msg` | Telugu | {detail} Press Try again, or type text below. | {detail} మళ్ళీ ప్రయత్నించండి నొక్కండి, లేదా కింద టెక్స్ట్ టైప్ చేయండి. | new |
| `err_insecure_title` | Telugu | The browser blocks the microphone on this address | ఈ అడ్రస్‌లో బ్రౌజర్ మైక్రోఫోన్‌ను అనుమతించదు | new |
| `err_insecure_msg` | Telugu | Open this page as http://127.0.0.1:{port}/ (not a LAN IP or computer name) so the browser allows the microphone. You can still type text below. | ఈ పేజీని http://127.0.0.1:{port}/ గా తెరవండి (LAN IP లేదా కంప్యూటర్ పేరుతో కాదు), అప్పుడు బ్రౌజర్ మైక్రోఫోన్‌ను అనుమతిస్తుంది. కింద టెక్స్ట్ టైప్ చేయవచ్చు. | new |
| `err_too_short_title` | Telugu | The recording was too short | రికార్డింగ్ చాలా చిన్నది | new |
| `err_too_short_msg` | Telugu | Speak for a second or two before pressing Stop, then press Try again. | ఆపండి నొక్కే ముందు ఒకటి రెండు సెకన్లు మాట్లాడి, మళ్ళీ ప్రయత్నించండి నొక్కండి. | new |
| `err_unexpected_title` | Telugu | Something went wrong | ఏదో తప్పు జరిగింది | adapted from elder-app `circle_error_generic` |
| `err_unexpected_msg` | Telugu | Please press Try again. | మళ్ళీ ప్రయత్నించండి. | adapted from elder-app `circle_error_generic` |
| `err_upstream_down_title` | Telugu | {service} is not running | {service} నడవడం లేదు | new |
| `err_upstream_down_msg` | Telugu | Run the start command again; it starts only what is missing. Then press Try again. | స్టార్ట్ కమాండ్‌ను మళ్ళీ నడపండి; లేనివాటిని మాత్రమే అది ప్రారంభిస్తుంది. తర్వాత మళ్ళీ ప్రయత్నించండి నొక్కండి. | new |
| `err_upstream_timeout_title` | Telugu | {service} took too long | {service} చాలా సమయం తీసుకుంది | new |
| `err_upstream_timeout_msg` | Telugu | The service may be overloaded. Wait a moment, then press Try again. | సేవపై భారం ఎక్కువ కావచ్చు. కొద్దిసేపు ఆగి, మళ్ళీ ప్రయత్నించండి నొక్కండి. | new |
| `err_upstream_warming_title` | Telugu | {service} is still warming up | {service} ఇంకా సిద్ధమవుతోంది | new |
| `err_upstream_warming_msg` | Telugu | The model is still loading. Wait a few seconds, then press Try again. | మోడల్ ఇంకా లోడ్ అవుతోంది. కొన్ని సెకన్లు ఆగి, మళ్ళీ ప్రయత్నించండి నొక్కండి. | new |
| `err_asr_en_unavailable_title` | Telugu | English speech needs the faster-whisper service | ఇంగ్లీష్ ప్రసంగానికి faster-whisper సేవ కావాలి | new |
| `err_asr_en_unavailable_msg` | Telugu | English recordings cannot be turned into text right now. You can still type English below, and Telugu and Hindi are not affected. Start the demo without “--asr-en off”, then press Try again. | ఇంగ్లీష్ రికార్డింగ్‌లను ఇప్పుడు అక్షరాలుగా మార్చలేం. కింద ఇంగ్లీష్ టైప్ చేయవచ్చు; తెలుగు, హిందీపై ప్రభావం లేదు. “--asr-en off” లేకుండా డెమోను ప్రారంభించి, మళ్ళీ ప్రయత్నించండి నొక్కండి. | new |
| `err_silent_title` | Telugu | The recording was silent | రికార్డింగ్ నిశ్శబ్దంగా ఉంది | new |
| `err_silent_msg` | Telugu | The microphone picked up almost nothing. Check it is not muted and speak a little closer, then press Try again. | మైక్రోఫోన్‌కు దాదాపు ఏమీ వినిపించలేదు. అది మ్యూట్‌లో లేదని చూసి, కొంచెం దగ్గరగా మాట్లాడి, మళ్ళీ ప్రయత్నించండి నొక్కండి. | new |
| `err_no_speech_title` | Telugu | No speech recognised | మాటలు గుర్తించబడలేదు | new |
| `err_no_speech_msg` | Telugu | The recording was heard but no words were recognised. Speak clearly and press Try again. | రికార్డింగ్ వినబడింది కానీ పదాలు గుర్తించబడలేదు. స్పష్టంగా మాట్లాడి, మళ్ళీ ప్రయత్నించండి నొక్కండి. | new |
| `page_title` | Hindi | SatSandesh voice demo | सत्संदेश वॉइस डेमो | new |
| `heading` | Hindi | SatSandesh voice demo | सत्संदेश वॉइस डेमो | new |
| `subheading` | Hindi | Speak a note in your language, read it in English, then send a reply and hear it spoken. | अपनी भाषा में एक संदेश बोलिए, उसे अंग्रेज़ी में पढ़िए, फिर जवाब भेजिए और उसे सुनिए। | new |
| `page_lang_label` | Hindi | Page language | पेज की भाषा | new |
| `page_lang_aria` | Hindi | Choose the page language | पेज की भाषा चुनें | new |
| `dep_asr` | Hindi | Telugu/Hindi speech | तेलुगु/हिंदी बोली | new |
| `dep_asr_en` | Hindi | English speech | अंग्रेज़ी बोली | new |
| `dep_mt` | Hindi | Translation | अनुवाद | new |
| `dep_render` | Hindi | Reply voice | जवाब की आवाज़ | new |
| `state_checking` | Hindi | checking… | जाँच हो रही है… | new |
| `state_ready` | Hindi | ready | तैयार | new |
| `state_warming` | Hindi | warming up… | तैयार हो रहा है… | new |
| `state_down` | Hindi | not running | चालू नहीं है | new |
| `state_off` | Hindi | turned off | बंद किया गया | new |
| `state_unknown` | Hindi | not responding | जवाब नहीं दे रहा | new |
| `host_banner` | Hindi | The microphone only works when this page is opened as http://127.0.0.1:{port}/ — not through a LAN IP or computer name. Typing text still works. | माइक्रोफ़ोन तभी चलता है जब यह पेज http://127.0.0.1:{port}/ के रूप में खोला गया हो — LAN IP या कंप्यूटर के नाम से नहीं। टेक्स्ट टाइप करना फिर भी काम करता है। | new |
| `speak_title` | Hindi | 1. Speak a note | 1. संदेश बोलिए | new |
| `speaker_lang_label` | Hindi | Language you will speak | आप किस भाषा में बोलेंगे | new |
| `lang_en` | Hindi | English | अंग्रेज़ी | new |
| `lang_te` | Hindi | Telugu | तेलुगु | new |
| `lang_hi` | Hindi | Hindi | हिंदी | new |
| `mic_start` | Hindi | Start recording | रिकॉर्डिंग शुरू करें | new |
| `mic_stop` | Hindi | Stop recording | रिकॉर्डिंग रोकें | new |
| `status_ready` | Hindi | Ready. Press Start recording, speak one sentence, then press Stop. | तैयार। “रिकॉर्डिंग शुरू करें” दबाइए, एक वाक्य बोलिए, फिर “रोकें” दबाइए। | new |
| `status_recording` | Hindi | Recording — speak now, then press Stop. | रिकॉर्ड हो रहा है — अब बोलिए, फिर रोकें दबाइए। | new |
| `status_recording_server` | Hindi | Recording on the console PC — speak now… {left}s left | कंसोल PC पर रिकॉर्ड हो रहा है — अब बोलिए… {left} सेकंड बचे | new |
| `status_elapsed` | Hindi | {what} {elapsed}s | {what} {elapsed} सेकंड | new |
| `status_done` | Hindi | Done. Press Start recording to try another note. | हो गया। दूसरा संदेश आज़माने के लिए रिकॉर्डिंग शुरू करें दबाइए। | new |
| `status_done_typed` | Hindi | Done. Type another sentence, or press Start recording. | हो गया। दूसरा वाक्य टाइप कीजिए, या रिकॉर्डिंग शुरू करें दबाइए। | new |
| `status_stopped` | Hindi | Stopped at the “{step}” step. | “{step}” चरण पर रुक गया। | new |
| `run_record` | Hindi | Recording — speak now… | रिकॉर्ड हो रहा है — अब बोलिए… | new |
| `run_upload` | Hindi | Sending the recording… | रिकॉर्डिंग भेजी जा रही है… | new |
| `run_transcribe` | Hindi | Listening to the recording… | रिकॉर्डिंग सुनी जा रही है… | new |
| `run_translate` | Hindi | Translating to English… | अंग्रेज़ी में अनुवाद हो रहा है… | new |
| `step_record` | Hindi | Record | रिकॉर्ड | new |
| `step_upload` | Hindi | Upload | भेजना | new |
| `step_transcribe` | Hindi | Transcribe | लिखना | new |
| `step_translate` | Hindi | Translate | अनुवाद | new |
| `step_render` | Hindi | Spoken reply | बोला हुआ जवाब | new |
| `step_skipped` | Hindi | skipped | छोड़ा गया | new |
| `meter_label` | Hindi | input level | आवाज़ का स्तर | new |
| `hint_no_sound` | Hindi | No sound is reaching the microphone — check it is not muted. | माइक्रोफ़ोन तक कोई आवाज़ नहीं पहुँच रही — देखिए कि वह म्यूट तो नहीं है। | new |
| `hint_auto_stop` | Hindi | Stopped automatically at {seconds} s. | {seconds} सेकंड पर अपने आप रुक गया। | new |
| `or_type` | Hindi | or type text directly | या सीधे टाइप कीजिए | new |
| `type_placeholder` | Hindi | Type or paste a sentence in the language selected above… | ऊपर चुनी गई भाषा में एक वाक्य टाइप कीजिए या चिपकाइए… | new |
| `type_aria` | Hindi | Text of the note | संदेश का टेक्स्ट | new |
| `type_button` | Hindi | Use this text | यह टेक्स्ट इस्तेमाल करें | new |
| `advanced_summary` | Hindi | Advanced: record on the console PC's own microphone | उन्नत: कंसोल PC के अपने माइक्रोफ़ोन से रिकॉर्ड करें | new |
| `record_button` | Hindi | Record ({seconds}s) | रिकॉर्ड ({seconds} सेकंड) | new |
| `advanced_note` | Hindi | Server-side recording; the browser microphone above is the normal path. | सर्वर की ओर की रिकॉर्डिंग; सामान्य तरीका ऊपर वाला ब्राउज़र माइक्रोफ़ोन है। | new |
| `conv_title` | Hindi | 2. The conversation | 2. बातचीत | new |
| `note_empty` | Hindi | Nothing yet. Record or type a note. | अभी कुछ नहीं है। कोई संदेश रिकॉर्ड कीजिए या टाइप कीजिए। | new |
| `reply_empty` | Hindi | The reply appears here. | जवाब यहाँ दिखेगा। | new |
| `note_working` | Hindi | Working on it… | काम चल रहा है… | new |
| `note_who` | Hindi | Voice note | वॉइस संदेश | new |
| `typed_tag` | Hindi | typed text | टाइप किया हुआ टेक्स्ट | new |
| `show_english` | Hindi | Show English | अंग्रेज़ी दिखाएँ | new |
| `hide_english` | Hindi | Hide English | अंग्रेज़ी छिपाएँ | new |
| `english_label` | Hindi | English | अंग्रेज़ी | new |
| `note_is_english` | Hindi | This note is already in English. | यह संदेश पहले से अंग्रेज़ी में है। | new |
| `produced_by` | Hindi | Written by {model} | {model} ने लिखा | new |
| `reply_title` | Hindi | 3. Family reply | 3. परिवार का जवाब | new |
| `reply_hint` | Hindi | Write the reply in English. It is translated and spoken in the language chosen below. | जवाब अंग्रेज़ी में लिखिए। उसका अनुवाद होगा और नीचे चुनी भाषा में सुनाया जाएगा। | new |
| `reply_placeholder` | Hindi | English reply to translate and speak… | अनुवाद करके सुनाने के लिए अंग्रेज़ी जवाब… | new |
| `reply_aria` | Hindi | English reply | अंग्रेज़ी जवाब | new |
| `reply_speak_in` | Hindi | Speak the reply in | जवाब किस भाषा में सुनाएँ | new |
| `reply_go` | Hindi | Translate and speak | अनुवाद करें और सुनाएँ | new |
| `reply_busy` | Hindi | Translating and speaking in {lang}… {elapsed}s | {lang} में अनुवाद करके सुनाया जा रहा है… {elapsed} सेकंड | new |
| `reply_done` | Hindi | Done. | हो गया। | new |
| `reply_done_text_only` | Hindi | Done — text only this time. | हो गया — इस बार सिर्फ़ टेक्स्ट। | new |
| `reply_stopped` | Hindi | Stopped at the spoken-reply step. | बोलकर सुनाने के चरण पर रुक गया। | new |
| `reply_who` | Hindi | Reply in {lang} | {lang} में जवाब | new |
| `play` | Hindi | Play | चलाएँ | new |
| `replay` | Hindi | Play again | फिर से चलाएँ | new |
| `audio_length` | Hindi | audio length {seconds} s | ऑडियो की लंबाई {seconds} सेकंड | new |
| `audio_unavailable` | Hindi | Audio is not available for this one — showing the text only. | इसके लिए ऑडियो उपलब्ध नहीं है — सिर्फ़ टेक्स्ट दिखाया जा रहा है। | new |
| `english_no_voice` | Hindi | English replies are text only: no English voice is installed. | अंग्रेज़ी जवाब सिर्फ़ टेक्स्ट में हैं: अंग्रेज़ी आवाज़ इंस्टॉल नहीं है। | new |
| `autoplay_blocked` | Hindi | Your browser blocked autoplay — press Play. | आपके ब्राउज़र ने अपने आप चलने से रोक दिया — चलाएँ दबाइए। | new |
| `audio_failed` | Hindi | Could not play the audio. Press Play to try again. | ऑडियो नहीं चल सका। फिर से कोशिश करने के लिए चलाएँ दबाइए। | new |
| `details_summary` | Hindi | Details (models and timings) | ब्यौरा (मॉडल और समय) | new |
| `details_services` | Hindi | Services | सेवाएँ | new |
| `details_models` | Hindi | Models | मॉडल | new |
| `details_latency` | Hindi | Time taken per step (measured, ms) | हर चरण में लगा समय (मापा हुआ, ms) | new |
| `details_none` | Hindi | No run yet. | अभी कोई रन नहीं हुआ। | new |
| `model_asr` | Hindi | Speech model | बोली पहचानने वाला मॉडल | new |
| `model_mt` | Hindi | Translation model | अनुवाद मॉडल | new |
| `model_translate` | Hindi | Reply translation model | जवाब का अनुवाद मॉडल | new |
| `model_voice` | Hindi | Voice | आवाज़ | new |
| `model_none` | Hindi | none (not needed) | कोई नहीं (ज़रूरत नहीं) | new |
| `lat_record` | Hindi | Record (microphone, wall clock) | रिकॉर्ड (माइक्रोफ़ोन, असली समय) | new |
| `lat_upload` | Hindi | Upload (browser → console, wall clock) | भेजना (ब्राउज़र → कंसोल, असली समय) | new |
| `lat_asr_decode` | Hindi | ASR — decode | ASR — डिकोड | new |
| `lat_asr_inference` | Hindi | ASR — inference | ASR — इन्फ़रेंस | new |
| `lat_asr_postprocess` | Hindi | ASR — postprocess | ASR — पोस्टप्रोसेस | new |
| `lat_asr_total` | Hindi | ASR — service total | ASR — सेवा का कुल | new |
| `lat_asr_round` | Hindi | ASR — round trip (console → service) | ASR — राउंड ट्रिप (कंसोल → सेवा) | new |
| `lat_mt_total` | Hindi | MT — service total | MT — सेवा का कुल | new |
| `lat_mt_round` | Hindi | MT — round trip (console → service) | MT — राउंड ट्रिप (कंसोल → सेवा) | new |
| `lat_render_total` | Hindi | Render — service total (translate + speak) | Render — सेवा का कुल (अनुवाद + आवाज़) | new |
| `lat_render_round` | Hindi | Render — round trip (console → service) | Render — राउंड ट्रिप (कंसोल → सेवा) | new |
| `svc_asr` | Hindi | Speech recognition | बोली की पहचान | new |
| `svc_asr_en` | Hindi | English speech recognition | अंग्रेज़ी बोली की पहचान | new |
| `svc_mt` | Hindi | Translation | अनुवाद | new |
| `svc_render` | Hindi | Spoken reply | बोला हुआ जवाब | new |
| `try_again` | Hindi | Try again | फिर से कोशिश करें | new |
| `err_console_down_title` | Hindi | The demo console is not responding | डेमो कंसोल जवाब नहीं दे रहा | new |
| `err_console_down_msg` | Hindi | The page could not reach its own backend. Is start_demo.py still running? Start it again if needed, then press Try again. | पेज अपने बैकएंड तक नहीं पहुँच सका। क्या start_demo.py अब भी चल रहा है? ज़रूरत हो तो उसे फिर शुरू कीजिए, फिर “फिर से कोशिश करें” दबाइए। | new |
| `err_timeout_title` | Hindi | The “{step}” step timed out | “{step}” चरण में बहुत देर लगी | new |
| `err_timeout_msg` | Hindi | No response in time. Check the service windows, then press Try again. | समय पर जवाब नहीं मिला। सेवाओं की विंडो देखिए, फिर “फिर से कोशिश करें” दबाइए। | new |
| `err_mic_blocked_title` | Hindi | Microphone access is blocked | माइक्रोफ़ोन की अनुमति रुकी हुई है | new |
| `err_mic_blocked_msg` | Hindi | Allow microphone access in the browser address bar, then press Try again. You can still type text below. | ब्राउज़र के एड्रेस बार में माइक्रोफ़ोन की अनुमति दीजिए, फिर “फिर से कोशिश करें” दबाइए। नीचे टेक्स्ट टाइप कर सकते हैं। | new |
| `err_no_mic_title` | Hindi | No microphone found | माइक्रोफ़ोन नहीं मिला | new |
| `err_no_mic_msg` | Hindi | No input device was found. Plug in or enable a microphone, then press Try again. You can still type text below. | कोई इनपुट डिवाइस नहीं मिला। माइक्रोफ़ोन लगाइए या चालू कीजिए, फिर “फिर से कोशिश करें” दबाइए। नीचे टेक्स्ट टाइप कर सकते हैं। | new |
| `err_mic_busy_title` | Hindi | The microphone is busy | माइक्रोफ़ोन अभी व्यस्त है | new |
| `err_mic_busy_msg` | Hindi | Another app may be using the microphone. Close it, then press Try again. | हो सकता है कोई और ऐप माइक्रोफ़ोन इस्तेमाल कर रहा हो। उसे बंद कीजिए, फिर “फिर से कोशिश करें” दबाइए। | new |
| `err_mic_start_title` | Hindi | Could not start the microphone | माइक्रोफ़ोन शुरू नहीं हो सका | new |
| `err_mic_start_msg` | Hindi | {detail} Press Try again, or type text below. | {detail} “फिर से कोशिश करें” दबाइए, या नीचे टेक्स्ट टाइप कीजिए। | new |
| `err_insecure_title` | Hindi | The browser blocks the microphone on this address | इस पते पर ब्राउज़र माइक्रोफ़ोन की अनुमति नहीं देता | new |
| `err_insecure_msg` | Hindi | Open this page as http://127.0.0.1:{port}/ (not a LAN IP or computer name) so the browser allows the microphone. You can still type text below. | इस पेज को http://127.0.0.1:{port}/ के रूप में खोलिए (LAN IP या कंप्यूटर के नाम से नहीं), तब ब्राउज़र माइक्रोफ़ोन की अनुमति देगा। नीचे टेक्स्ट टाइप कर सकते हैं। | new |
| `err_too_short_title` | Hindi | The recording was too short | रिकॉर्डिंग बहुत छोटी थी | new |
| `err_too_short_msg` | Hindi | Speak for a second or two before pressing Stop, then press Try again. | रोकें दबाने से पहले एक-दो सेकंड बोलिए, फिर “फिर से कोशिश करें” दबाइए। | new |
| `err_unexpected_title` | Hindi | Something went wrong | कुछ गड़बड़ हो गई | new |
| `err_unexpected_msg` | Hindi | Please press Try again. | कृपया फिर से कोशिश करें। | new |
| `err_upstream_down_title` | Hindi | {service} is not running | {service} चालू नहीं है | new |
| `err_upstream_down_msg` | Hindi | Run the start command again; it starts only what is missing. Then press Try again. | स्टार्ट कमांड फिर चलाइए; वह सिर्फ़ वही शुरू करता है जो गायब है। फिर “फिर से कोशिश करें” दबाइए। | new |
| `err_upstream_timeout_title` | Hindi | {service} took too long | {service} में बहुत देर लगी | new |
| `err_upstream_timeout_msg` | Hindi | The service may be overloaded. Wait a moment, then press Try again. | हो सकता है सेवा पर बहुत बोझ हो। थोड़ा रुकिए, फिर “फिर से कोशिश करें” दबाइए। | new |
| `err_upstream_warming_title` | Hindi | {service} is still warming up | {service} अभी तैयार हो रहा है | new |
| `err_upstream_warming_msg` | Hindi | The model is still loading. Wait a few seconds, then press Try again. | मॉडल अभी लोड हो रहा है। कुछ सेकंड रुकिए, फिर “फिर से कोशिश करें” दबाइए। | new |
| `err_asr_en_unavailable_title` | Hindi | English speech needs the faster-whisper service | अंग्रेज़ी बोली के लिए faster-whisper सेवा चाहिए | new |
| `err_asr_en_unavailable_msg` | Hindi | English recordings cannot be turned into text right now. You can still type English below, and Telugu and Hindi are not affected. Start the demo without “--asr-en off”, then press Try again. | अंग्रेज़ी रिकॉर्डिंग अभी टेक्स्ट में नहीं बदली जा सकती। नीचे अंग्रेज़ी टाइप कर सकते हैं; तेलुगु और हिंदी पर कोई असर नहीं है। डेमो को “--asr-en off” के बिना शुरू कीजिए, फिर “फिर से कोशिश करें” दबाइए। | new |
| `err_silent_title` | Hindi | The recording was silent | रिकॉर्डिंग खामोश थी | new |
| `err_silent_msg` | Hindi | The microphone picked up almost nothing. Check it is not muted and speak a little closer, then press Try again. | माइक्रोफ़ोन को लगभग कुछ सुनाई नहीं दिया। देखिए कि वह म्यूट तो नहीं है, थोड़ा पास आकर बोलिए, फिर “फिर से कोशिश करें” दबाइए। | new |
| `err_no_speech_title` | Hindi | No speech recognised | कोई बोली पहचानी नहीं गई | new |
| `err_no_speech_msg` | Hindi | The recording was heard but no words were recognised. Speak clearly and press Try again. | रिकॉर्डिंग सुनी गई लेकिन कोई शब्द पहचाने नहीं गए। साफ़ बोलिए और “फिर से कोशिश करें” दबाइए। | new |
