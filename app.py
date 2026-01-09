import streamlit as st
import yfinance as yf
import pandas as pd
from fpdf import FPDF
from datetime import datetime
import sys
import time

# --- FUNCTIES: DATA OPHALEN ---

def get_val(df, row_name, col_idx=0):
    if row_name in df.index:
        val = df.loc[row_name].iloc[col_idx]
        return val if pd.notna(val) else 0
    return 0

@st.cache_data(ttl=3600)
def get_data(ticker):
    print(f"Start ophalen data voor: {ticker}")
    
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        if not info: return None, "Geen data."
            
        fin = stock.financials
        if fin.empty: return None, "Geen financiële cijfers."
            
        bal = stock.balance_sheet
        cf = stock.cashflow
        
        # 0. VALUTA
        price_curr = info.get('currency', 'USD')
        fin_curr = info.get('financialCurrency', price_curr)
        conversion_rate = 1.0
        currency_label = price_curr 
        
        if price_curr != fin_curr:
            fx_ticker = f"{fin_curr}{price_curr}=X"
            try:
                time.sleep(0.5) 
                fx = yf.Ticker(fx_ticker)
                hist = fx.history(period="1d")
                if not hist.empty:
                    conversion_rate = hist['Close'].iloc[-1]
                    currency_label = f"{price_curr} (ex {fin_curr})"
            except:
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

        # 2. ANALISTEN & FRENKEL METRIC
        peg_ratio = info.get('pegRatio', None)
        pe_ratio = info.get('trailingPE', info.get('forwardPE', 0))
        earnings_growth = info.get('earningsGrowth', None)
        revenue_growth = info.get('revenueGrowth', None)
        
        wall_street_growth = 0
        growth_source = "N/A"
        
        if earnings_growth and 0 < earnings_growth < 2.0:
            wall_street_growth = earnings_growth
            growth_source = "Earnings"
        elif revenue_growth and revenue_growth > 0:
            wall_street_growth = revenue_growth
            growth_source = "Revenue"
        elif peg_ratio and peg_ratio > 0 and pe_ratio > 0:
            wall_street_growth = pe_ratio / peg_ratio
            growth_source = "PEG Implied"
        
        if wall_street_growth == 0 and earnings_growth:
             wall_street_growth = earnings_growth
             growth_source = "Earnings Raw"

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
        
        # --- DE FRENKEL METER (EXPECTED RETURN) ---
        # Formule: FCF Yield + Verwachte Groei (Analist of Sustainable)
        growth_proxy = wall_street_growth if wall_street_growth > 0 else sustainable_growth
        expected_return = fcf_yield + growth_proxy
        
        # --- CLUSTER LOGICA ---
        cluster = "NEUTRAAL (Hold)"
        reason = "Waardering en kwaliteit zijn in balans."
        
        is_cannibal = reinvestment_rate < 0 and roic > 0.15
        
        if is_cannibal:
            if implied_growth < 0.10:
                cluster = "BUY (Quality Cannibal)"
                reason = "Topkwaliteit + eigen inkoop aandelen."
            else:
                cluster = "HOLD (Expensive Cannibal)"
                reason = "Topkwaliteit, maar geprijsd voor perfectie."
        elif implied_growth > 0.10: 
            if calc_peg and 0.5 < calc_peg < 2.0:
                cluster = "BUY (Aggressive Growth)"
                reason = "Duur, maar explosieve groei verwacht (PEG < 2)."
            else:
                cluster = "SPECULATIEF / TE DUUR"
                reason = "Markt prijst >10% groei in. Pas op."
        elif implied_growth < sustainable_growth and roic > 0.15:
            cluster = "BUY (Cluster 1)"
            reason = "Margin of Safety: Kan harder groeien dan markt verwacht."
        elif roic < 0.08:
            cluster = "AVOID (Value Trap)"
            reason = "Bedrijf vernietigt waarde."

        result = {
            'meta': {
                'ticker': ticker,
                'price': price,
                'currency': currency_label,
                'cluster': cluster,
                'reason': reason,
                'wall_street': ws_growth_str,
                'growth_src': growth_source,
                'exp_return': expected_return # Voor narrative
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
                'Exp.Ret': f"{expected_return:.1%}", # NIEUW!
                'Analyst': ws_growth_str,
                'PEG': peg_display,
                'ROIC': f"{roic:.1%}",
                'ROIIC': f"{roiic:.1%}",
                'Cluster': cluster
            }
        }
        return result, None

    except Exception as e:
        return None, str(e)

# --- PDF GENERATOR ---
class PDFReport(FPDF):
    def header(self):
        self.set_fill_color(0, 51, 102) 
        self.rect(0, 0, 210, 25, 'F')
        self.set_font("Arial", 'B', 16)
        self.set_text_color(255, 255, 255)
        self.cell(0, 10, "Taco & Frenkel - Investment Screener", ln=True, align='R')
        self.set_font("Arial", '', 10)
        datum = datetime.now().strftime("%d-%m-%Y %H:%M")
        self.cell(0, 0, f"Gegenereerd op: {datum}", ln=True, align='R')
        self.ln(20)

    def footer(self):
        self.set_y(-15)
        self.set_font("Arial", 'I', 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Pagina {self.page_no()}", 0, 0, 'C')

def clean_text(text):
    replacements = { "⚠️": "LET OP:", "🚀": "", "€": "EUR", "✅": "CHECK:", "💡": "TIP:" }
    for k, v in replacements.items():
        text = str(text).replace(k, v)
    return text.encode('latin-1', 'replace').decode('latin-1')

def generate_narrative(data):
    raw = data['raw']
    meta = data['meta']
    text = f"ANALYSIS FOR {meta['ticker']}:\n"
    
    # De Frenkel-Nuance
    text += f"VERWACHTINGEN: Ons model berekent dat het bedrijf {data['display']['Imp.Gr']} moet groeien voor 10% rendement. "
    text += f"Als we echter uitgaan van de verwachte groei ({meta['wall_street']}), is het Totaal Verwacht Rendement: {data['display']['Exp.Ret']} per jaar.\n"
    
    if raw['roic'] > 0.20: text += f"\nKWALITEIT: Uitmuntend (ROIC {raw['roic']:.1%}). "
    elif raw['roic'] < 0.08: text += f"\nKWALITEIT: Zorgwekkend (ROIC {raw['roic']:.1%}). "
    else: text += f"\nKWALITEIT: Solide (ROIC {raw['roic']:.1%}). "

    if raw['reinvest'] < 0: 
        text += f"Het bedrijf is een 'Quality Cannibal' (inkoop eigen aandelen). "
    elif raw['reinvest'] > 0.80 and raw['roiic'] > 0.15: 
        text += f"Agressieve investeringen voor groei ({raw['reinvest']:.1%} reinvestment). "
    
    text += f"\n\nCONCLUSIE: {meta['cluster']} - {meta['reason']}"
    return clean_text(text)

def create_pdf(results_list):
    pdf = PDFReport()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.alias_nb_pages()
    
    # 1. TABEL
    pdf.add_page()
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, "1. Market Overview", ln=True)
    pdf.ln(5)
    pdf.set_fill_color(240, 240, 240)
    pdf.set_font("Arial", 'B', 7)
    # Kolommen aangepast voor Exp.Ret
    w = [15, 15, 15, 15, 15, 15, 15, 15, 15, 45] 
    headers = ["Ticker", "Price", "Valuta", "Imp.Gr", "Exp.Ret", "PEG", "ROIC", "ROIIC", "Analyst", "Cluster"]
    for i, h in enumerate(headers):
        pdf.cell(w[i], 8, h, 1, 0, 'C', fill=True)
    pdf.ln()
    pdf.set_font("Arial", '', 7)
    for item in results_list:
        d = item['display']
        # Volgorde moet kloppen met headers!
        row_data = [d['Ticker'], d['Price'], d['Valuta'].split(' ')[0], d['Imp.Gr'], d['Exp.Ret'], d['PEG'], d['ROIC'], d['ROIIC'], d['Analyst'], clean_text(d['Cluster'])]
        for i, val in enumerate(row_data):
            pdf.cell(w[i], 8, str(val).encode('latin-1', 'replace').decode('latin-1'), 1, 0, 'C')
        pdf.ln()

    # 2. ANALYSES
    pdf.ln(10)
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, "2. Gedetailleerde Analyse", ln=True)
    for item in results_list:
        if pdf.get_y() > 230: pdf.add_page()
        meta = item['meta']
        display = item['display']
        narrative = generate_narrative(item)
        pdf.ln(5)
        pdf.set_fill_color(230, 240, 255)
        pdf.set_font("Arial", 'B', 11)
        pdf.cell(0, 8, f"{clean_text(meta['ticker'])}  |  {clean_text(display['Price'])} {meta['currency']}", ln=True, fill=True, border=1)
        pdf.set_font("Arial", '', 10)
        pdf.multi_cell(0, 6, narrative, border='L R B')
        pdf.ln(2)

    # 3. METHODOLOGIE
    pdf.add_page()
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, "Bijlage: Methodologie (Met Frenkel-Metric)", ln=True)
    pdf.set_font("Arial", '', 9)
    explanation = """
Dit rapport hanteert twee perspectieven op waarde:

PERSPECTIEF 1: "WAT IS NODIG?" (De Taco-Methode)
Variabele: Implied Growth.
Vraag: Ik WIL 10% rendement. Hoe hard MOET het bedrijf groeien om dat te geven?
Als Implied Growth (bijv 4%) lager is dan wat ze kunnen (15%), is het een koopje.

PERSPECTIEF 2: "WAT KRIJG IK?" (De Frenkel-Methode)
Variabele: Expected Return (Exp.Ret).
Vraag: Als de analisten gelijk hebben, wat is dan mijn totaalrendement per jaar?
Formule: FCF Yield + Analyst Growth Estimate.
Voorbeeld: 3% dividend/buybacks + 12% winstgroei = 15% Totaal Rendement.

CONCLUSIE
We zoeken aandelen waar Perspectief 1 'VEILIG' zegt (lage implied growth) en Perspectief 2 'RIJK' zegt (hoog expected return).
    """
    pdf.multi_cell(0, 5, clean_text(explanation))
    
    return pdf.output(dest='S').encode('latin-1', 'replace') 

# --- DE APP UI ---
st.set_page_config(page_title="Screener & Report", layout="wide")
st.title("🚀 Taco & Frenkel - Investment Screener (V23)")
st.info("**Instructies:** Tickers komma gescheiden (NVO, ASML). Voor lokaal: .AS of .CO gebruiken.")
tickers_input = st.text_area("Tickers:", "NVO, LLY, ASML")

if st.button("🚀 Genereer Rapport"):
    print(f"👀 BEZOEK ALERT! Iemand zoekt nu op: {tickers_input} -- {datetime.now()}", flush=True)
    
    tickers_list = [t.strip().upper() for t in tickers_input.split(',')]
    full_results = []
    table_data = []
    
    progress_bar = st.progress(0)
    
    for i, ticker in enumerate(tickers_list):
        data, error_msg = get_data(ticker)
        
        if data:
            full_results.append(data)
            table_data.append(data['display'])
        else:
            st.error(f"❌ Fout bij {ticker}: {error_msg}")
        
        if i < len(tickers_list) - 1:
            time.sleep(2) 
            
        progress_bar.progress((i + 1) / len(tickers_list))
    
    if full_results:
        st.subheader("📊 Live Resultaten")
        st.dataframe(pd.DataFrame(table_data), hide_index=True)
        pdf_bytes = create_pdf(full_results)
        st.download_button("📄 Download Rapport (PDF)", pdf_bytes, f"Investment_Report_{datetime.now().strftime('%Y%m%d')}.pdf", "application/pdf")