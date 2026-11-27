# Higgsfield integration

Locked stack for MacroPulse Instagram Reels (2026-11-26):

| Role | Choice |
|------|--------|
| Platform | Higgsfield API (`https://api.higgsfield.ai`) |
| Primary | Seedance 2.5 **reference-to-video** |
| Face series | Kling 3.0 Pro Elements |
| Identity | Soul ID (`POST /v1/custom-references`) |

## Fable vs video

Cursor **Fable** models are coding agents. They do **not** generate video. Use this module for photoreal motion.

## Credentials

Official (docs):

```bash
export HF_API_KEY_ID=...
export HF_API_KEY_SECRET=...
```

Aliases accepted by `instagram/higgsfield.py`:

```bash
export HIGGSFIELD_API_KEY=...          # or key_id:key_secret
export HIGGSFIELD_API_SECRET=...
# optional after setup
export HIGGSFIELD_SOUL_ID=...
export HIGGSFIELD_KLING_ELEMENT_ID=...
```

Never commit values. Create keys in Higgsfield Console:
https://docs.higgsfield.ai/docs/authentication

## Flow

1. Optional: upload local `--identity` stills via Files API (`/files/generate-upload-url`).
2. Optional once: train Soul ID (`--train-soul-id`).
3. **Seedance 2.5 R2V** with identity + desk/env refs, `aspect_ratio=9:16`.
4. Download `video.url` → `output/instagram/reel.mp4`.
5. Soft caption + Instagram Graph **dry-run** (human approval before live).

Without credentials: writes `higgsfield_stub.json` and optionally falls back to `--keyframe` MoviePy or Phase B placeholders (`--fallback`).

## Creative

Reference: https://www.instagram.com/p/Df7WYmlyJMu/  
Founder in chair; Cursor chatting then MacroPulse builds; casual desk outfit; soft brand.

Identity photos live only in the Project store — never in git.
