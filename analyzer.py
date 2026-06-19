import os
from typing import Literal
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv() 

# Define the category choices
MarketCategory = Literal[
    "RBI",
    "FED",
    "OIL",
    "GEOPOLITICAL",
    "GOVERNMENT_POLICY",
    "ELECTION",
    "BANKING",
    "GLOBAL_MARKETS",
    "OTHER"
]

class MarketAnalysis(BaseModel):
    event: str = Field(description="A standardized name for this specific news event to group similar stories together (e.g., 'Israel-Iran Conflict').")
    event_type: MarketCategory = Field(description="Classify the headline into exactly one of the allowed categories based on its primary subject matter.")
    impact_score: int = Field(description="Impact score from 0 (no impact) to 100 (extreme impact).")
    is_high_impact: bool = Field(description="Must be strictly true if impact_score is 80 or higher, otherwise false.")
    nifty_direction: str = Field(description="Expected Nifty 50 direction (e.g., Bullish, Bearish, Neutral).")
    banknifty_direction: str = Field(description="Expected BankNifty direction.")
    vix_impact: str = Field(description="Expected impact on India VIX (e.g., Spike, Crush, Flat).")
    confidence: int = Field(description="Confidence percentage of this forecast (0 to 100).")
    summary: str = Field(description="A brief 1-2 sentence summary of the news.")
    reasoning: str = Field(description="Reasoning behind the given F&O directions and impact scores.")

def analyze_headline(headline: str) -> str:
    client = genai.Client()

    system_instruction = (
        "You are a professional Indian macro and F&O analyst. "
        "Analyze the provided global or domestic news headline and determine its expected "
        "impact on the Indian equity derivatives market (Nifty, BankNifty, and India VIX). "
        "Your output must be strictly in the requested JSON format."
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=headline,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=MarketAnalysis,
            temperature=0.1, 
        ),
    )

    return response.text