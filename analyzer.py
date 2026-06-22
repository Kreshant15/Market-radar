import os
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()

class MarketAnalysis(BaseModel):
    event: str = Field(description="Short, standardized name of the event (e.g., 'US CPI Data', 'RBI Repo Rate').")
    event_type: str = Field(description="Must be MACRO, RBI, FED, GEOPOLITICAL, FII_DII, or IGNORE (for corporate news)")
    impact_score: int = Field(description="0-100 scale. Give 0 if event_type is IGNORE.")
    confidence: int = Field(description="AI confidence in this analysis (0-100)")
    nifty_direction: str = Field(description="Expected impact on Nifty 50. Must be: BULLISH, BEARISH, or NEUTRAL")
    banknifty_direction: str = Field(description="Expected impact on Bank Nifty. Must be: BULLISH, BEARISH, or NEUTRAL")
    direction_probability: str = Field(description="Strict Percentage (e.g., '80%') based on historical reaction to similar data.")
    event_region: str = Field(description="Must be GLOBAL, INDIAN, or HEAVYWEIGHT")
    vix_impact: str = Field(description="Expected impact on India VIX. Must be: SPIKE, CRUSH, or STABLE")
    suggested_strategy: str = Field(description="Specific F&O options strategy (e.g. Bull Put Spread, Short Straddle)")
    strategy_hedging: str = Field(description="Risk management rules for this specific trade")
    reasoning: str = Field(description="Explain WHY, specifically referencing how the market reacted to this type of data in previous years.")
    affected_sector: str = Field(description="Specific sector affected, or 'Broader Market'")
    affected_stock: str = Field(description="Specific company mentioned, or 'None'")
    target_ticker: str = Field(description="Yahoo Finance ticker (e.g., 'RELIANCE.NS'). 'NONE' if no specific stock.")
    micro_strategy: str = Field(description="Targeted stock-specific options strategy. 'N/A' if none.")

def analyze_headline(headline: str) -> str:
    """Uses Gemini to analyze news with strict Macro and Probability rules."""
    client = genai.Client()
    
    prompt = (
        f"Analyze the following Indian/Global financial news headline:\n\n"
        f"Headline: {headline}\n\n"
        f"🚨 CRITICAL DESK INSTRUCTIONS 🚨\n"
        f"1. NOISE FILTER: The trading desk HATES corporate news 'mess'. If this is a standard corporate announcement, fund stake buy, minor PR, or stock-specific earnings, YOU MUST set event_type to 'IGNORE' and impact_score to 0.\n"
        f"   -> 👑 HEAVYWEIGHT EXCEPTION: If the news is specifically about 'Reliance' or 'HDFC Bank', NEVER ignore it. You MUST classify it, set event_region to 'HEAVYWEIGHT', and give it an impact_score of at least 50.\n"
        f"2. HISTORICAL PROBABILITY: If this is a Macro event (US Fed, RBI, CPI, NFP, GDP, FII/DII, Crude, War), evaluate the `direction_probability` as a strict percentage (e.g., '75%'). Calculate this based on previous years' historical data (how markets usually react to rate cuts, inflation spikes, etc.).\n"
        f"3. REASONING: Your reasoning MUST mention the historical context (e.g., 'Historically, lower US CPI results in a 80% probability of FII inflows into emerging markets like India...').\n"
        f"4. REGION CLASSIFICATION: You MUST classify `event_region` as:\n"
        f"   - 'HEAVYWEIGHT' if the news is specifically about Reliance Industries or HDFC Bank.\n"
        f"   - 'GLOBAL' if the news is about US Fed, Crude Oil, Geopolitics, US CPI, Global Markets, etc.\n"
        f"   - 'INDIAN' if the news is about RBI, India CPI, Indian Govt, FII/DII, or broader Nifty/BankNifty.\n\n"
        f"When suggesting an options strategy:\n"
        f"- If Bullish + VIX Spike: Bull Call Spread / Long Calls.\n"
        f"- If Bearish + VIX Spike: Bear Put Spread / Long Puts.\n"
        f"- If Neutral + VIX Crush: Iron Condor / Short Straddle.\n"
        f"- If Bullish + VIX Crush/Stable: Bull Put Spread (Credit).\n"
    )
    
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=MarketAnalysis,
            temperature=0.1, # Extremely low temp for rigid, mathematical outputs
        ),
    )
    
    return response.text