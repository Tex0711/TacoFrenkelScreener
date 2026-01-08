import streamlit as st
import yfinance as yf
import pandas as pd
from fpdf import FPDF
from datetime import datetime

# --- FUNCTIES: DATA OPHALEN ---
def get_val(df, row_name, col_idx=0):
    if row_name in df.index:
        val = df.loc[row_name].iloc[col_idx]
        return val if pd.notna(val) else 0
    return 0

def get_data(ticker):
    # Debug: Print dat we beginnen
    print(f"Start ophalen {ticker}...")
    
    try:
        stock = yf.Ticker(ticker)
        
        # Forceer data ophalen om verbinding te testen
        info = stock.info
        if not info:
            return None, "Geen 'info' ontvangen van Yahoo. Ticker bestaat mogelijk niet of Yahoo blokkeert."
            
        fin = stock.financials
        if fin.empty: 
            return None, "Geen financiële cijfers gevonden (financials dataframe is leeg)."
            
        bal = stock.balance_sheet
        cf = stock.cashflow
        
        # 0. VALUTA CHECK
        price_curr = info.get('currency', 'USD')
        fin_curr = info.get('financialCurrency', price_curr)
        conversion_rate = 1.0
        currency_label = price_curr 
        
        if price_curr != fin_curr:
            fx_ticker = f"{fin_curr}{price_curr}=X"
            try:
                fx = yf.Ticker(fx_ticker)
                hist = fx.history(period="1d")
                if not hist.empty:
                    conversion_rate = hist['Close'].iloc[-1]
                    currency_label = f"{price_curr} (ex {fin_curr})"
            except Exception as e:
                # Valuta fout is niet fataal, we gaan door
                print(f"Valuta conversie fout: {e}")
                pass 

        # 1. WAARDERING
        ocf = get_val(cf, 'Operating Cash Flow', 0) * conversion_rate
        if 'Capital Expenditure' in cf.index:
            capex = get_val(cf, 'Capital Expenditure', 0) * conversion_rate
        else:
            capex = get_val(cf, 'Capital Expenditures', 0) * conversion_rate
        if capex == 0: capex = get_val(cf, 'Purchase Of PPE', 0) * conversion_rate

        fcf = ocf + capex if capex < 0 else ocf - capex
            
        price = info.get('currentPrice', 0)
        shares = info.get('sharesOutstanding', 0)
        market_cap = price * shares
        
        fcf_yield = fcf / market_cap if market_cap else 0
        implied_growth = (0.10 - fcf_yield)

        # 2. ANALISTEN DATA
        peg_ratio = info.get('pegRatio', None)
        pe_ratio = info.get('trailingPE', info.get('forwardPE', 0))
        earnings_growth = info.get('earningsGrowth', None)
        revenue_growth = info.get('revenueGrowth', None)
        
        wall_street_growth = 0
        growth_source = "N/A"
        
        if earnings_growth and 0 < earnings_growth < 2.0:
            wall_street_growth = earnings_growth
            growth_source = "Earnings Est."
        elif revenue_growth and revenue_growth > 0:
            wall_street_growth = revenue_growth
            growth_source = "Revenue Est."
        elif peg_ratio and peg_ratio > 0 and pe_ratio > 0:
            wall_street_growth = pe_ratio / peg_ratio
            growth_source = "Implied by PEG"
        
        if wall_street_growth == 0 and earnings_growth:
             wall_street_growth = earnings_growth
             growth_source = "Earnings (Raw)"

        calc_peg = None
        if wall_street_growth > 0 and pe_ratio > 0:
            calc_peg = pe_ratio / (wall_street_growth * 100)

        ws_growth_str = f"{wall_street_growth:.1%}" if wall_street_growth > 0 else "N/A"
        peg_display = f"{calc_peg:.2f}" if calc_peg else "N/A"

        # 3. KWALITEIT
        ebit = get_val(fin, 'EBIT', 0)
        pretax = get_val(fin, 'Pretax Income', 0)
        tax = get_val(fin, 'Tax Provision', 0)
        tax_rate = tax / pretax if pretax != 0 else 0.21
        nopat = ebit * (1 - tax_rate)
        
        debt = get_val(bal, 'Total Debt', 0)
        equity = get_val(bal, 'Stockholders Equity', 0)
        cash = get_val(bal, 'Cash Cash Equivalents And Short Term Investments', 0)
        if cash == 0: cash = get_val(bal, 'Cash And Cash Equivalents', 0)
        
        ic = debt + equity - cash
        roic = nopat / ic if ic != 0 else 0

        # 4. TOEKOMST
        ebit_prev = get_val(fin, 'EBIT', 1)
        pretax_prev = get_val(fin, 'Pretax Income', 1)
        tax_prev = get_val(fin, 'Tax Provision', 1)
        tr_prev = tax_prev / pretax_prev if pretax_prev != 0 else 0.21
        nopat_prev = ebit_prev * (1 - tr_prev)
        
        debt_prev = get_val(bal, 'Total Debt', 1)
        eq_prev = get_val(bal, 'Stockholders Equity', 1)
        c_prev = get_val(bal, 'Cash Cash Equivalents And Short Term Investments', 1)
        if c_prev == 0: c_prev = get_val(bal, 'Cash And Cash Equivalents', 1)
        ic_prev = debt_prev + eq_prev - c_prev
        
        delta_nopat = nopat - nopat_prev
        delta_ic = ic - ic_prev
        
        roiic = delta_nopat / delta_ic if delta_ic > 0 else 0
        reinvestment_rate = delta_ic / nopat if nopat > 0 else 0
        sustainable_growth = roic * reinvestment_rate
        
        # --- CLUSTER LOGICA ---
        cluster = "NEUTRAAL (Hold)"
        reason = "Waardering en kwaliteit zijn in balans."
        
        is_cannibal = reinvestment_rate < 0 and roic > 0.15
        
        if is_cannibal:
            if implied_growth < 0.10:
                cluster = "BUY (Quality Cannibal)"
                reason = "Topkwaliteit (hoge ROIC) die eigen aandelen inkoopt. Prijs is redelijk."
            else:
                cluster = "HOLD (Expensive Cannibal)"
                reason = "Topkwaliteit, maar de markt verwacht al >10% groei."
        elif implied_growth > 0.10: 
            if calc_peg and 0.5 < calc_peg < 2.0:
                cluster = "BUY (Aggressive Growth)"
                reason = "Duur (Impl.Gr > 10%), maar analisten verwachten explosieve groei (PEG < 2)."
            else:
                cluster = "SPECULATIEF / TE DUUR"
                reason = "Markt prijst >10% groei in. PEG ratio ondersteunt dit niet overtuigend."
        elif implied_growth < sustainable_growth and roic > 0.15:
            cluster = "BUY (Cluster 1)"
            reason = "Markt verwacht minder groei dan bedrijf aankan (Margin of Safety)."
        elif roic < 0.08:
            cluster = "AVOID (Value Trap)"
            reason = "Bedrijf vernietigt waarde (ROIC < 8%)."

        result = {
            'meta': {
                'ticker': ticker,
                'price': price,
                'currency': currency_label,
                'cluster': cluster,
                'reason': reason,
                'wall_street': ws_growth_str,
                'peg_raw': peg_display,
                'growth_src': growth_source
            },
            'raw': {
                'fcf_yield': fcf_yield,
                'implied_growth': implied_growth,
                'roic': roic,
                'roiic': roiic,
                'reinvest': reinvestment_rate,
                'sus_growth': sustainable_growth
            },
            'display': {
                'Ticker': ticker,
                'Price': f"{price:.2f}",
                'Valuta': currency_label,
                'Imp.Gr': f"{implied_growth:.1%}",
                'Analyst Gr': ws_growth_str,
                'PEG (Est)': peg_display,
                'ROIC': f"{roic:.1%}",
                'ROIIC': f"{roiic:.1%}",
                'Max Gr': f"{sustainable_growth:.1%}",
                'Cluster': cluster
            }
        }
        return result, None # Geen error

    except Exception as e:
        return None, str(e) # Retourneer de specifieke foutmelding

# --- PDF GENERATOR ---
class PDFReport(FPDF):
    def header(self):
        self.set_fill_color(0, 51, 102) 
        self.rect(0, 0, 210, 25, 'F')
        self.set_font("Arial", 'B', 16)
        self.
