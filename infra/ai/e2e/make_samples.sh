#!/usr/bin/env bash
# Makes two test recordings, in a throwaway container (espeak-ng + ffmpeg):
#   speech.webm -- synthesized English speech, as WebM/Opus (the container a
#                  browser's MediaRecorder produces)
#   tone.webm   -- a 2 s sine tone: audio with NO speech in it
# usage: make_samples.sh <output dir>
#
# These are generated, not recorded by a person or by Chrome: the speech is a
# robotic voice, and the WebM is muxed by ffmpeg, not by MediaRecorder. They are
# enough to prove the wiring (real ASR on real WebM/Opus bytes); they are not
# evidence about accuracy on an elder's voice.
set -euo pipefail
OUT="$(cd "$1" && pwd)"
docker run --rm -v "$OUT":/out python:3.11-slim sh -c '
  apt-get update -qq >/dev/null 2>&1
  apt-get install -y -qq --no-install-recommends espeak-ng ffmpeg >/dev/null 2>&1
  espeak-ng -v en -s 135 "Hello everyone. When is the satsang today? Please bring some flowers." -w /out/speech.wav
  ffmpeg -nostdin -loglevel error -y -i /out/speech.wav -c:a libopus -f webm /out/speech.webm
  ffmpeg -nostdin -loglevel error -y -f lavfi -i "sine=frequency=440:duration=2" -c:a libopus -f webm /out/tone.webm
  rm -f /out/speech.wav
  ls -l /out
'
