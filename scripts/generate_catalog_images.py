#!/usr/bin/env python3
"""Generate a small reusable tourism catalog image set with Azure OpenAI."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

from PIL import Image

DEFAULT_ENDPOINT = "https://ai-eastus2508770413322.openai.azure.com"
DEFAULT_DEPLOYMENT = "gpt-image-1-mini"
DEFAULT_API_VERSION = "2025-04-01-preview"

PROMPTS = {
    "hoi-an-lantern-evening": (
        "Premium editorial travel photography of Hoi An ancient town in Vietnam "
        "at blue hour, warm silk lanterns reflecting on the river, a small wooden "
        "boat, elegant tropical atmosphere, authentic architecture, natural people "
        "in the distance, cinematic but realistic, no text, no logos"
    ),
    "ba-na-hills-golden-bridge": (
        "Premium realistic travel photography of the Golden Bridge at Ba Na Hills "
        "near Da Nang Vietnam, giant stone hands, soft mountain mist, lush tropical "
        "hills, early morning light, a few natural visitors, no text, no logos"
    ),
    "cham-islands-snorkeling": (
        "Premium realistic travel photography of a snorkeling day at Cham Islands "
        "Vietnam, clear turquoise tropical water, traditional boat, coral visible "
        "beneath the surface, relaxed travellers, bright natural daylight, no text"
    ),
    "basket-boat-coconut-forest": (
        "Premium realistic travel photography of round Vietnamese basket boats "
        "moving through the green nipa palm waterways of Cam Thanh near Hoi An, "
        "joyful family experience, warm tropical light, no text, no logos"
    ),
    "my-son-sanctuary-sunrise": (
        "Premium realistic travel photography of My Son Sanctuary Vietnam at "
        "sunrise, ancient red brick Cham temple towers, jungle mist, soft golden "
        "light, peaceful historical atmosphere, no text, no logos"
    ),
    "da-nang-street-food": (
        "Premium editorial travel photography of a vibrant Da Nang street food "
        "table at night, banh xeo, herbs, noodles and tropical drinks, warm market "
        "lights, friendly local host in the background, realistic, no text"
    ),
    "hue-imperial-day-trip": (
        "Premium realistic travel photography of Hue Imperial City Vietnam, ornate "
        "red gates, traditional royal architecture, reflective water and gardens, "
        "soft overcast tropical light, elegant and calm, no text, no logos"
    ),
    "han-river-sunset-cruise": (
        "Premium realistic travel photography from a boutique boat on the Han River "
        "in Da Nang at sunset, Dragon Bridge and modern skyline glowing, tropical "
        "drinks on a table, calm water, sophisticated atmosphere, no text"
    ),
    "marble-mountains-caves": (
        "Premium realistic travel photography inside a luminous cave in Marble "
        "Mountains Da Nang Vietnam, stone steps and a small pagoda, shafts of "
        "sunlight, tropical greenery at the cave mouth, adventurous, no text"
    ),
    "hoi-an-cooking-class": (
        "Premium editorial travel photography of a Vietnamese cooking class near "
        "Hoi An, colourful herbs and vegetables, guests preparing fresh spring "
        "rolls in an airy tropical garden kitchen, authentic and joyful, no text"
    ),
    "son-tra-wildlife-sunrise": (
        "Premium realistic travel photography of Son Tra Peninsula near Da Nang at "
        "sunrise, lush jungle road above the sea, a red-shanked douc langur visible "
        "on a branch, atmospheric golden light, no text, no logos"
    ),
    "vietnamese-spa-ritual": (
        "Premium editorial hospitality photography of an elegant Vietnamese herbal "
        "spa ritual, warm natural wood, tropical leaves, herbal compresses, soft "
        "window light, calm luxury near the beach, no text, no logos, no nudity"
    ),
}


def get_access_token() -> str:
    configured = os.getenv("AZURE_OPENAI_ACCESS_TOKEN")
    if configured:
        return configured

    result = subprocess.run(
        [
            "az",
            "account",
            "get-access-token",
            "--resource",
            "https://cognitiveservices.azure.com",
            "--query",
            "accessToken",
            "--output",
            "tsv",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def generate(
    endpoint: str,
    deployment: str,
    api_version: str,
    prompt: str,
) -> bytes:
    url = (
        f"{endpoint.rstrip('/')}/openai/deployments/{deployment}"
        f"/images/generations?api-version={api_version}"
    )
    body = json.dumps(
        {
            "prompt": prompt,
            "n": 1,
            "size": "1536x1024",
            "quality": "low",
            "output_format": "jpeg",
        }
    ).encode()
    payload = None
    for attempt in range(6):
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {get_access_token()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError as error:
            details = error.read().decode(errors="replace")
            if error.code != 429 or attempt == 5:
                raise RuntimeError(
                    f"Image generation failed: {error.code} {details}"
                ) from error
            retry_after = int(error.headers.get("Retry-After", "0") or 0)
            delay = max(retry_after, min(30, 2 ** (attempt + 1)))
            print(f"Rate limited; retrying in {delay}s")
            time.sleep(delay)

    if payload is None:
        raise RuntimeError("Image generation returned no response.")

    image = payload["data"][0].get("b64_json")
    if not image:
        raise RuntimeError("The image response did not contain b64_json.")
    source = Image.open(BytesIO(base64.b64decode(image))).convert("RGB")
    output = BytesIO()
    source.save(output, format="WEBP", quality=88, method=6)
    return output.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=sorted(PROMPTS))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("frontend/public/assets/catalog"),
    )
    parser.add_argument(
        "--endpoint",
        default=os.getenv("AZURE_OPENAI_ENDPOINT", DEFAULT_ENDPOINT),
    )
    parser.add_argument(
        "--deployment",
        default=os.getenv("AZURE_OPENAI_IMAGE_DEPLOYMENT", DEFAULT_DEPLOYMENT),
    )
    parser.add_argument(
        "--api-version",
        default=os.getenv("AZURE_OPENAI_API_VERSION", DEFAULT_API_VERSION),
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    selected = (
        {args.only: PROMPTS[args.only]} if args.only else PROMPTS
    )

    for index, (slug, prompt) in enumerate(selected.items(), start=1):
        target = args.output / f"{slug}.webp"
        if target.exists() and not args.force:
            print(f"[{index}/{len(selected)}] Keeping {target}")
            continue
        print(f"[{index}/{len(selected)}] Generating {slug}")
        target.write_bytes(
            generate(args.endpoint, args.deployment, args.api_version, prompt)
        )
        if index < len(selected):
            time.sleep(2)


if __name__ == "__main__":
    main()
