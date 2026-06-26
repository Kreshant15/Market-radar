import os
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()

class MarketAnalysis(BaseModel):
    headline_analyzed: str = Field(description="The exact original headline this analysis belongs to")
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
    macro_actual_data: str = Field(description="If a macro event was just released (CPI, Repo Rate, PCE), the exact numerical data reported (e.g., '4.1%'). If upcoming or N/A, put 'N/A'.")
    macro_forecast_data: str = Field(description="The expected consensus number (e.g., '2.0%'). If N/A, put 'N/A'.")
    macro_rate_impact: str = Field(description="Strictly: '⬆️ RATE HIKE EXPECTED', '⬇️ RATE CUT EXPECTED', or '➖ NEUTRAL'. Put 'N/A' if not a macro event.")

class BatchMarketAnalysis(BaseModel):
    analyses: list[MarketAnalysis]

def analyze_headlines_batch(headlines: list[str]) -> str:
    """Uses Gemini to analyze a BATCH of news headlines in a single API call to save tokens."""
    if not headlines:
        return '{"analyses": []}'

    client = genai.Client()
    
    # Format the list of headlines into a numbered text block for the AI
    headlines_text = "\n".join([f"{i+1}. {hl}" for i, hl in enumerate(headlines)])
    
    prompt = (
        f"Analyze the following BATCH of Indian/Global financial news headlines:\n\n"
        f"{headlines_text}\n\n"
        f"🚨 CRITICAL DESK INSTRUCTIONS 🚨\n"
        f"Provide an individual analysis object for EACH headline provided above.\n"
        f"1. NOISE FILTER: The trading desk HATES corporate news 'mess'. If this is a standard corporate announcement, fund stake buy, minor PR, or stock-specific earnings, YOU MUST set event_type to 'IGNORE' and impact_score to 0.\n"
        f"   -> 👑 HEAVYWEIGHT EXCEPTION: If the news is specifically about 'Reliance' or 'HDFC Bank', NEVER ignore it. You MUST classify it, set event_region to 'HEAVYWEIGHT', and give it an impact_score of at least 50.\n"
        f"   -> 📰 MARKET RECAP EXCEPTION: The desk wants end-of-day market summaries (e.g., 'Sensex gains 300 points', 'Nifty ends higher'). For these, DO NOT ignore. Set event_type to 'RECAP', impact_score to 50, AND strictly set the event name to exactly 'Daily Market Recap' to prevent duplicates.\n"
        f"2. HISTORICAL PROBABILITY: If this is a Macro event (US Fed, RBI, CPI, NFP, GDP, FII/DII, Crude, War), evaluate the `direction_probability` as a strict percentage (e.g., '75%'). Calculate this based on previous years' historical data (how markets usually react to rate cuts, inflation spikes, etc.).\n"
        f"3. REASONING: Your reasoning MUST mention the historical context.\n"
        f"4. REGION CLASSIFICATION: You MUST classify `event_region` as 'HEAVYWEIGHT', 'GLOBAL', or 'INDIAN'.\n\n"
        f"🚨 NEW NERO UPGRADE FOR MACRO DATA 🚨\n"
        f"If the headline mentions macroeconomic data (like CPI, PCE, Repo Rate, Jobless Claims) that has just been released or is upcoming:\n"
        f" - You MUST extract the exact numerical value and put it in `macro_actual_data`.\n"
        f" - You MUST extract or estimate the consensus expectation and put it in `macro_forecast_data`.\n"
        f" - You MUST determine the central bank impact and put it in `macro_rate_impact` (e.g., '⬆️ RATE HIKE EXPECTED').\n\n"
        f"When suggesting an options strategy:\n"
        f"- If Bullish + VIX Spike: Bull Call Spread / Long Calls.\n"
        f"- If Bearish + VIX Spike: Bear Put Spread / Long Puts.\n"
        f"- If Neutral + VIX Crush: Iron Condor / Short Straddle.\n"
        f"- If Bullish + VIX Crush/Stable: Bull Put Spread (Credit).\n"
    )
    
    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=BatchMarketAnalysis,
                temperature=0.1, 
            ),
        )
        return response.text
    except Exception as e:
        print(f"Batch Analysis API Error: {e}")
        return '{"analyses": []}'