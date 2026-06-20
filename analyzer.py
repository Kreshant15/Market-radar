import os
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()

# Define the structured output schema for options strategies
class MarketAnalysis(BaseModel):
    event: str = Field(description="Short concise name of the event")
    event_type: str = Field(description="Must be one of: MACRO, REGULATORY, CORPORATE, GLOBAL, OTHER")
    impact_score: int = Field(description="A score from 0 (no impact) to 100 (catastrophic/historic market-moving event)")
    confidence: int = Field(description="How confident you are in this analysis from 0 to 100")
    nifty_direction: str = Field(description="Must be: BULLISH, BEARISH, or NEUTRAL")
    banknifty_direction: str = Field(description="Must be: BULLISH, BEARISH, or NEUTRAL")
    vix_impact: str = Field(description="Expected impact on India VIX. Must be: SPIKE, CRUSH, or STABLE")
    suggested_strategy: str = Field(description="Specific F&O options strategy (e.g. Bull Call Spread, Bear Put Spread, Iron Condor, Short Straddle, Long Put, etc.) tailored to the market direction and VIX sentiment.")
    strategy_hedging: str = Field(description="Risk management rules for this specific trade (e.g. stop loss triggers, profit targets, or leg adjustments)")
    reasoning: str = Field(description="A comprehensive, detailed 2-3 sentence analysis of why this event causes this sentiment, index movement, and VIX volatility.")
    affected_sector: str = Field(description="Specific sector affected (e.g., 'IT', 'Pharma', 'Banking') or 'Broader Market'")
    affected_stock: str = Field(description="Specific company mentioned (e.g., 'Reliance', 'TCS') or 'None'")
    target_ticker: str = Field(description="Yahoo Finance ticker for the specific stock/sector (e.g., 'RELIANCE.NS', '^CNXIT'). Use 'NONE' if no specific stock/sector.")
    micro_strategy: str = Field(description="Targeted stock-specific options strategy (e.g., 'TCS Bull Call Spread'). 'N/A' if none.")

def analyze_headline(headline: str) -> str:
    """Uses Gemini to analyze news headlines with strict JSON structures."""
    client = genai.Client()
    
    prompt = (
        f"Analyze the following Indian financial news headline and evaluate its potential impact "
        f"on the Nifty 50, Bank Nifty, India VIX, and options trading positions.\n\n"
        f"Headline: {headline}\n\n"
        f"When suggesting an options strategy:\n"
        f"- If Bullish with a VIX Spike: suggest a Bull Call Spread or Long Calls.\n"
        f"- If Bearish with a VIX Spike: suggest a Bear Put Spread or Long Puts.\n"
        f"- If Neutral with a VIX Crush (premiums melting): suggest an Iron Condor or Short Straddle.\n"
        f"- If Bullish with a VIX Crush/Stable: suggest a Bull Put Spread (credit spread).\n"
        f"MICRO ENGINE RULES:\n"
        f"- Identify if a specific Sector or Stock is deeply affected.\n"
        f"- Provide its EXACT Yahoo Finance ticker (e.g., 'RELIANCE.NS', 'HDFCBANK.NS', '^CNXIT' for IT sector). Indian stocks must end with '.NS'.\n"
        f"- Formulate a targeted micro_strategy for that specific stock/sector."
    )
    
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=MarketAnalysis,
            temperature=0.2, # Lower temperature for analytical and consistent results
        ),
    )
    
    return response.text