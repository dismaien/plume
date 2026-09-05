"""WhisperX alignment/output with timestamp decoding over the complete input.

This entry point changes only the ASR method, inside this subprocess. It does not
edit the installed WhisperX package. VAD must not remove quiet source intervals.
"""
from dataclasses import asdict
from pathlib import Path
import json


def decode_full_audio(pipeline, audio, args):
    """Keep the original sample timeline; let Whisper decide where speech ends."""
    if isinstance(audio, str):
        from whisperx.audio import load_audio
        audio = load_audio(audio)

    temperature = args["temperature"]
    increment = args.get("temperature_increment_on_fallback")
    if increment is not None and increment > 0:
        temperatures = []
        value = temperature
        while value <= 1.000001:
            temperatures.append(round(value, 6))
            value += increment
        temperature = temperatures

    suppress_tokens = [int(value) for value in args["suppress_tokens"].split(",")]
    if args.get("suppress_numerals"):
        from whisperx.asr import find_numeral_symbol_tokens
        from faster_whisper.tokenizer import Tokenizer
        tokenizer = Tokenizer(pipeline.model.hf_tokenizer,
                              pipeline.model.model.is_multilingual,
                              task=args["task"], language=args.get("language") or "en")
        suppress_tokens += find_numeral_symbol_tokens(tokenizer)

    segments, info = pipeline.model.transcribe(
        audio, language=args.get("language"), task=args["task"],
        beam_size=args["beam_size"], best_of=args["best_of"],
        patience=args["patience"], length_penalty=args["length_penalty"],
        temperature=temperature,
        compression_ratio_threshold=args["compression_ratio_threshold"],
        log_prob_threshold=args["logprob_threshold"],
        no_speech_threshold=args["no_speech_threshold"],
        condition_on_previous_text=args["condition_on_previous_text"],
        initial_prompt=args.get("initial_prompt"),
        suppress_tokens=suppress_tokens,
        without_timestamps=False, word_timestamps=True, vad_filter=False,
    )
    raw = [asdict(segment) for segment in segments]
    result = {"language": info.language, "segments": [
        {"start": s["start"], "end": s["end"], "text": s["text"]} for s in raw
    ]}
    diagnostics = {
        "decoder": "faster_whisper_full_audio", "vad_filter": False,
        "word_timestamps": True, "input_duration_seconds": len(audio) / 16000,
        "language": info.language, "segments": raw,
        "temperature": temperature,
        "logprob_threshold": args["logprob_threshold"],
        "no_speech_threshold": args["no_speech_threshold"],
    }
    return result, diagnostics


def main():
    import whisperx.asr
    import whisperx.transcribe
    from whisperx.__main__ import cli

    original_task = whisperx.transcribe.transcribe_task

    def task(args, parser):
        # Capture parsed settings before upstream consumes them with pop().
        settings = dict(args)
        counter = 0

        def transcribe(pipeline, audio, **kwargs):
            nonlocal counter
            settings["language"] = args.get("language")  # normalized by WhisperX's CLI task
            print("Full-audio Whisper decoding: source timeline retained; VAD cuts disabled", flush=True)
            result, diagnostics = decode_full_audio(pipeline, audio, settings)
            directory = Path(settings["output_dir"]) / "decoding-diagnostics"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"asr-{counter}.json").write_text(
                json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
            counter += 1
            return result

        whisperx.asr.FasterWhisperPipeline.transcribe = transcribe
        return original_task(args, parser)

    whisperx.transcribe.transcribe_task = task
    cli()


if __name__ == "__main__":
    main()
