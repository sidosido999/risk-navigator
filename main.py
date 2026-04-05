import streamlit as st
import pandas as pd
import requests
import json
import os
import time
from datetime import datetime
import plotly.graph_objects as go

st.set_page_config(
    page_title="리스크 관리 네비게이터",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
body, .stApp { background-color: #0e1117; color: #ffffff; }
.main .block-container { padding: 1rem; max-width: 100%; }
h1 { font-size: clamp(1.2rem, 4vw, 2rem) !important; }
h2, h3 { font-size: clamp(1rem, 3vw, 1.5rem) !important; }
.stMetric { background: #1e2130; border-radius: 10px; padding: 10px; }
.stMetric label { font-size: clamp(0.7rem, 2.5vw, 0.9rem) !important; }
.stMetric [data-testid="metric-container"] > div { font-size: clamp(1rem, 3vw, 1.5rem) !important; }
.critical-banner {
    background: linear-gradient(135deg, #ff000033, #ff000066);
    border: 2px solid #ff0000;
    border-radius: 10px;
    padding: 15px;
    text-align: center;
    font-size: clamp(1rem, 3vw, 1.3rem);
    font-weight: bold;
    color: #ff4444;
    animation: blink 1s infinite;
}
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.5} }
.signal-critical { color: #ff4444; font-weight: bold; }
.signal-warning { color: #ffaa00; font-weight: bold; }
.signal-normal { color: #00ff88; font-weight: bold; }
.section-title {
    font-size: clamp(1rem, 3vw, 1.2rem);
    font-weight: bold;
    border-left: 4px solid #4a9eff;
    padding-left: 10px;
    margin: 15px 0 10px 0;
}
div[data-testid="stButton"] button {
    min-height: 44px;
    font-size: clamp(0.8rem, 2.5vw, 1rem);
    width: 100%;
}
div[data-testid="stNumberInput"] input,
div[data-testid="stTextInput"] input {
    font-size: clamp(0.8rem, 2.5vw, 1rem);
    min-height: 40px;
}
.stDataFrame { font-size: clamp(0.65rem, 2vw, 0.85rem); }
@media (max-width: 768px) {
    .main .block-container { padding: 0.5rem; }
    .stMetric { padding: 6px; }
}
</style>
""", unsafe_allow_html=True)

KIS_REAL_DOMAIN = "https://openapi.koreainvestment.com:9443"
DATA_FILE = "data.json"
RATE_LIMIT_SEC = 0.22

# secrets 로드
try:
    APP_KEY = st.secrets["app_key"]
    APP_SECRET = st.secrets["app_secret"]
    ACC_NO_FULL = st.secrets["acc_no"]
    ACC_NO = ACC_NO_FULL.replace("-", "")
    CANO = ACC_NO[:8] if len(ACC_NO) >= 8 else ACC_NO
    ACNT_PRDT_CD = ACC_NO[8:10] if len(ACC_NO) >= 10 else "01"
    SECRETS_OK = True
except Exception:
    SECRETS_OK = False
    APP_KEY = APP_SECRET = ACC_NO_FULL = ""
    CANO = ACNT_PRDT_CD = ""

# session_state 초기화
defaults = {
    "holdings": [],
    "cash_balance": 10000000,
    "target_mdd": 20,
    "last_api_call": 0.0,
    "token_cache": {},
    "data_loaded": False
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

def load_data_file():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                d = json.load(f)
            st.session_state.holdings = d.get("holdings", [])
            st.session_state.cash_balance = d.get("cash_balance", 10000000)
            st.session_state.target_mdd = d.get("target_mdd", 20)
        except Exception:
            pass

def save_data_file():
    try:
        with open(DATA_FILE, "w") as f:
            json.dump({
                "holdings": st.session_state.holdings,
                "cash_balance": st.session_state.cash_balance,
                "target_mdd": st.session_state.target_mdd
            }, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

if not st.session_state.data_loaded:
    load_data_file()
    st.session_state.data_loaded = True

def _rate_limit():
    now = time.time()
    elapsed = now - st.session_state.last_api_call
    if elapsed < RATE_LIMIT_SEC:
        time.sleep(RATE_LIMIT_SEC - elapsed)
    st.session_state.last_api_call = time.time()

def get_access_token():
    cache = st.session_state.token_cache
    now = time.time()
    if cache.get("token") and cache.get("expires_at", 0) - now > 300:
        return cache["token"]
    if not SECRETS_OK:
        return None
    try:
        _rate_limit()
        resp = requests.post(
            f"{KIS_REAL_DOMAIN}/oauth2/tokenP",
            headers={"Content-Type": "application/json"},
            json={"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            token = data.get("access_token")
            expires_in = int(data.get("expires_in", 86400))
            st.session_state.token_cache = {
                "token": token,
                "expires_at": now + expires_in
            }
            return token
        else:
            st.error(f"🔑 토큰 발급 실패 (HTTP {resp.status_code}) — App Key/Secret을 확인하세요.")
            return None
    except Exception as e:
        st.error(f"🔑 토큰 발급 실패 (HTTP N/A) — App Key/Secret을 확인하세요.")
        return None

def _kis_get(tr_id, path, params):
    token = get_access_token()
    if not token:
        return None
    try:
        _rate_limit()
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET,
            "tr_id": tr_id,
            "custtype": "P"
        }
        resp = requests.get(
            f"{KIS_REAL_DOMAIN}{path}",
            headers=headers,
            params=params,
            timeout=10
        )
        if resp.status_code == 429:
            time.sleep(1)
            return _kis_get(tr_id, path, params)
        if resp.status_code == 401:
            st.session_state.token_cache = {}
            return _kis_get(tr_id, path, params)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("rt_cd") != "0":
            return None
        return data
    except Exception:
        return None

def _safe_float(val):
    try:
        return float(str(val).replace(",", "").replace("%", ""))
    except Exception:
        return 0.0

@st.cache_data(ttl=60)
def fetch_stock_price(ticker, _app_key, _app_secret):
    token = get_access_token()
    if not token:
        return None
    try:
        _rate_limit()
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": _app_key,
            "appsecret": _app_secret,
            "tr_id": "FHKST01010100",
            "custtype": "P"
        }
        resp = requests.get(
            f"{KIS_REAL_DOMAIN}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=headers,
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
            timeout=10
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("rt_cd") != "0":
            return None
        o = data.get("output", {})
        return {
            "price": _safe_float(o.get("stck_prpr", 0)),
            "change_rate": _safe_float(o.get("prdy_ctrt", 0)),
            "name": o.get("hts_kor_isnm", ticker),
            "per": _safe_float(o.get("per", 0)),
            "pbr": _safe_float(o.get("pbr", 0)),
            "volume": _safe_float(o.get("acml_vol", 0)),
            "high52": _safe_float(o.get("d250_hgpr", 0)),
            "low52": _safe_float(o.get("d250_lwpr", 0)),
            "ma20": _safe_float(o.get("d20_dsrt", 0))
        }
    except Exception:
        return None

@st.cache_data(ttl=60)
def fetch_kospi_index(_app_key, _app_secret):
    token = get_access_token()
    if not token:
        return None
    try:
        _rate_limit()
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": _app_key,
            "appsecret": _app_secret,
            "tr_id": "FHPUP02100000",
            "custtype": "P"
        }
        resp = requests.get(
            f"{KIS_REAL_DOMAIN}/uapi/domestic-stock/v1/quotations/inquire-index-price",
            headers=headers,
            params={"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001"},
            timeout=10
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("rt_cd") != "0":
            return None
        o = data.get("output", {})
        return {
            "index": _safe_float(o.get("bstp_nmix_prpr", 0)),
            "change_rate": _safe_float(o.get("bstp_nmix_prdy_ctrt", 0))
        }
    except Exception:
        return None

@st.cache_data(ttl=60)
def fetch_investor_trend(ticker, _app_key, _app_secret):
    token = get_access_token()
    if not token:
        return None
    try:
        _rate_limit()
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": _app_key,
            "appsecret": _app_secret,
            "tr_id": "FHKST01010900",
            "custtype": "P"
        }
        resp = requests.get(
            f"{KIS_REAL_DOMAIN}/uapi/domestic-stock/v1/quotations/inquire-investor",
            headers=headers,
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
            timeout=10
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("rt_cd") != "0":
            return None
        rows = data.get("output", [])
        result = []
        for r in rows[:5]:
            result.append({
                "날짜": r.get("stck_bsop_date", ""),
                "외인순매수": _safe_float(r.get("frgn_ntby_qty", 0)),
                "기관순매수": _safe_float(r.get("orgn_ntby_qty", 0)),
                "개인순매수": _safe_float(r.get("indv_ntby_qty", 0))
            })
        return result
    except Exception:
        return None

@st.cache_data(ttl=60)
def fetch_market_investor(_app_key, _app_secret):
    token = get_access_token()
    if not token:
        return None
    try:
        _rate_limit()
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": _app_key,
            "appsecret": _app_secret,
            "tr_id": "FHKST03020200",
            "custtype": "P"
        }
        resp = requests.get(
            f"{KIS_REAL_DOMAIN}/uapi/domestic-stock/v1/quotations/inquire-member",
            headers=headers,
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "0001", "FID_DIV_CLS_CODE": "0"},
            timeout=10
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("rt_cd") != "0":
            return None
        o = data.get("output", {})
        return {
            "외인": _safe_float(o.get("frgn_ntby_qty", 0)),
            "기관": _safe_float(o.get("orgn_ntby_qty", 0)),
            "개인": _safe_float(o.get("indv_ntby_qty", 0))
        }
    except Exception:
        return None

def calc_stock_return(current_price, avg_price):
    if avg_price <= 0:
        return 0.0
    return ((current_price - avg_price) / avg_price) * 100

def calc_kospi_return(current_kospi, buy_kospi):
    if buy_kospi <= 0:
        return 0.0
    return ((current_kospi - buy_kospi) / buy_kospi) * 100

def calc_relative_signal(stock_ret, kospi_ret):
    return stock_ret - kospi_ret

def get_signal_status(gap):
    if gap <= -10:
        return "🔴 CRITICAL", "critical"
    elif gap <= -5:
        return "🟡 WARNING", "warning"
    else:
        return "🟢 NORMAL", "normal"

def calc_position_size(total_assets, price, stop_price, risk_pct=0.02):
    if price <= stop_price or price <= 0:
        return 0
    risk_amount = total_assets * risk_pct
    risk_per_share = price - stop_price
    return int(risk_amount / risk_per_share)

def can_add_buy(stock_ret, ma20):
    return stock_ret >= 5 and ma20 > 100

# Sidebar
with st.sidebar:
    st.markdown("### 🛡️ 리스크 네비게이터")
    st.caption("KIS OpenAPI 연동 리스크 관리 도구")
    st.divider()

    st.markdown("#### 💰 계좌 설정")
    new_cash = st.number_input("현금 잔고 (원)", min_value=0, value=st.session_state.cash_balance, step=100000)
    if new_cash != st.session_state.cash_balance:
        st.session_state.cash_balance = new_cash
        save_data_file()

    new_mdd = st.slider("목표 최대 낙폭 (MDD) %", 5, 50, st.session_state.target_mdd)
    if new_mdd != st.session_state.target_mdd:
        st.session_state.target_mdd = new_mdd
        save_data_file()

    st.divider()
    st.markdown("#### 📋 보유 종목 추가")
    with st.form("add_holding_form", clear_on_submit=True):
        t_ticker = st.text_input("종목 코드 (6자리)", value="005930")
        t_name = st.text_input("종목명 (직접 입력)", value="삼성전자")
        t_avg = st.number_input("평단가 (원)", min_value=1, value=70000, step=100)
        t_qty = st.number_input("수량 (주)", min_value=1, value=10, step=1)
        t_kospi = st.number_input("매수 시점 코스피", min_value=100.0, value=2500.0, step=1.0)
        submitted = st.form_submit_button("➕ 종목 추가", use_container_width=True)
        if submitted:
            st.session_state.holdings.append({
                "ticker": t_ticker.strip(),
                "name": t_name.strip(),
                "avg_price": float(t_avg),
                "quantity": int(t_qty),
                "buy_kospi": float(t_kospi)
            })
            save_data_file()
            st.success(f"✅ {t_name} 추가 완료!")
            st.rerun()

    if st.session_state.holdings:
        st.divider()
        st.markdown("#### 📌 보유 종목 목록")
        for i, h in enumerate(st.session_state.holdings):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.caption(f"{h['ticker']} {h['name']}")
            with col2:
                if st.button("🗑️", key=f"del_{i}", use_container_width=True):
                    st.session_state.holdings.pop(i)
                    save_data_file()
                    st.rerun()

    st.divider()
    auto_refresh = st.checkbox("🔄 1분 자동 새로고침", value=False)

# 메인 대시보드
if not SECRETS_OK:
    st.warning("⚠️ Streamlit Secrets에 app_key, app_secret, acc_no가 설정되지 않았습니다.\nStreamlit Cloud → Secrets 탭에서 해당 값을 등록해 주세요.")

now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
st.markdown(f"<p style='color:#888;font-size:0.85rem'>기준 시각: {now_str}</p>", unsafe_allow_html=True)

# 시장 현황
st.markdown('<div class="section-title">📊 시장 현황</div>', unsafe_allow_html=True)
kospi_data = fetch_kospi_index(APP_KEY, APP_SECRET)
market_inv = fetch_market_investor(APP_KEY, APP_SECRET)

kospi_val = kospi_data["index"] if kospi_data else 0.0
kospi_chg = kospi_data["change_rate"] if kospi_data else 0.0
frgn = market_inv["외인"] if market_inv else 0.0
orgn = market_inv["기관"] if market_inv else 0.0
indv = market_inv["개인"] if market_inv else 0.0

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("🇰🇷 KOSPI", f"{kospi_val:,.2f}" if kospi_val else "조회...", f"{kospi_chg:+.2f}%" if kospi_chg else "↑ —")
with col2:
    st.metric("🌍 외인", f"{frgn:+,.0f}" if frgn else "조회...", "↑ —" if frgn >= 0 else "↓ —")
with col3:
    st.metric("🏦 기관", f"{orgn:+,.0f}" if orgn else "조회...", "↑ —" if orgn >= 0 else "↓ —")
with col4:
    st.metric("👤 개인", f"{indv:+,.0f}" if indv else "조회...", "↑ —" if indv >= 0 else "↓ —")

st.divider()

# 포트폴리오
st.markdown('<div class="section-title">📁 내 포트폴리오</div>', unsafe_allow_html=True)

if not st.session_state.holdings:
    st.info("좌측 사이드바에서 보유 종목을 추가해 주세요.")
else:
    portfolio_rows = []
    critical_list = []
    total_eval = 0.0
    total_buy = 0.0

    for h in st.session_state.holdings:
        ticker = h["ticker"]
        name = h["name"]
        avg_price = h["avg_price"]
        quantity = h["quantity"]
        buy_kospi = h["buy_kospi"]

        stock_data = fetch_stock_price(ticker, APP_KEY, APP_SECRET)
        current_price = stock_data["price"] if stock_data else 0.0
        change_rate = stock_data["change_rate"] if stock_data else 0.0
        per = stock_data["per"] if stock_data else 0.0
        pbr = stock_data["pbr"] if stock_data else 0.0
        volume = stock_data["volume"] if stock_data else 0.0
        ma20 = stock_data["ma20"] if stock_data else 0.0

        eval_amount = current_price * quantity
        buy_amount = avg_price * quantity
        pl_amount = eval_amount - buy_amount
        total_eval += eval_amount
        total_buy += buy_amount

        stock_ret = calc_stock_return(current_price, avg_price)
        kospi_ret = calc_kospi_return(kospi_val, buy_kospi)
        gap = calc_relative_signal(stock_ret, kospi_ret)
        signal_label, signal_cls = get_signal_status(gap)
        add_buy = "✅ 가능" if can_add_buy(stock_ret, ma20) else "❌ 불가"

        if signal_cls == "critical":
            critical_list.append(name)

        portfolio_rows.append({
            "종목": f"{name}({ticker})",
            "현재가(원)": int(current_price),
            "평단가(원)": int(avg_price),
            "수량": quantity,
            "평가(원)": int(eval_amount),
            "손익(원)": int(pl_amount),
            "수익률(%)": round(stock_ret, 2),
            "코스피등락(%)": round(kospi_ret, 2),
            "상대Gap(%)": round(gap, 2),
            "신호": signal_label,
            "PER": per,
            "PBR": pbr,
            "불타기": add_buy
        })

    if critical_list:
        st.markdown(f'<div class="critical-banner">🚨 즉시 매도 검토: {", ".join(critical_list)} — 상대 수익률 -10% 이하!</div>', unsafe_allow_html=True)
        st.markdown("")

    df_port = pd.DataFrame(portfolio_rows)

    def style_signal(val):
        if "CRITICAL" in str(val):
            return "color:#ff4444;font-weight:bold"
        elif "WARNING" in str(val):
            return "color:#ffaa00;font-weight:bold"
        elif "NORMAL" in str(val):
            return "color:#00ff88;font-weight:bold"
        return ""

    def style_gap(val):
        try:
            v = float(val)
            if v <= -10:
                return "color:#ff4444;font-weight:bold"
            elif v <= -5:
                return "color:#ffaa00"
            elif v >= 0:
                return "color:#00ff88"
        except Exception:
            pass
        return ""

    styled_df = df_port.style\
        .map(style_signal, subset=["신호"])\
        .map(style_gap, subset=["상대Gap(%)"])\
        .format({
            "현재가(원)": "{:,}",
            "평단가(원)": "{:,}",
            "평가(원)": "{:,}",
            "손익(원)": "{:,}",
            "수익률(%)": "{:.2f}%",
            "코스피등락(%)": "{:.2f}%",
            "상대Gap(%)": "{:.2f}%"
        })

    st.dataframe(styled_df, use_container_width=True)

    cash = st.session_state.cash_balance
    total_assets = total_eval + cash
    total_pl = total_eval - total_buy

    st.divider()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📈 총 평가금액", f"{total_eval:,.0f}원")
    c2.metric("💵 현금", f"{cash:,.0f}원")
    c3.metric("💹 총 손익", f"{total_pl:+,.0f}원")
    c4.metric("🏦 총 자산", f"{total_assets:,.0f}원")

    st.divider()
    st.markdown('<div class="section-title">📉 수익률 비교 차트</div>', unsafe_allow_html=True)
    if portfolio_rows:
        names = [r["종목"] for r in portfolio_rows]
        stock_rets = [r["수익률(%)"] for r in portfolio_rows]
        kospi_rets = [r["코스피등락(%)"] for r in portfolio_rows]
        gaps = [r["상대Gap(%)"] for r in portfolio_rows]

        fig = go.Figure()
        fig.add_trace(go.Bar(name="종목수익률(%)", x=names, y=stock_rets, marker_color="#4a9eff"))
        fig.add_trace(go.Bar(name="코스피등락(%)", x=names, y=kospi_rets, marker_color="#aaaaaa"))
        fig.add_trace(go.Scatter(name="상대Gap(%)", x=names, y=gaps, mode="lines+markers", line=dict(color="#ff4444", width=2)))
        fig.add_hline(y=-10, line_dash="dash", line_color="red", annotation_text="-10% 손절선")
        fig.update_layout(
            barmode="group",
            paper_bgcolor="#0e1117",
            plot_bgcolor="#0e1117",
            font=dict(color="#ffffff"),
            legend=dict(orientation="h", y=-0.2),
            margin=dict(l=10, r=10, t=30, b=60),
            height=320
        )
        st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.markdown('<div class="section-title">👥 외인·기관 투자자 동향</div>', unsafe_allow_html=True)
    for h in st.session_state.holdings:
        trend = fetch_investor_trend(h["ticker"], APP_KEY, APP_SECRET)
        if trend:
            df_trend = pd.DataFrame(trend)
            fig2 = go.Figure()
            fig2.add_trace(go.Bar(name="외인", x=df_trend["날짜"], y=df_trend["외인순매수"], marker_color="#4a9eff"))
            fig2.add_trace(go.Bar(name="기관", x=df_trend["날짜"], y=df_trend["기관순매수"], marker_color="#ff9944"))
            fig2.add_trace(go.Bar(name="개인", x=df_trend["날짜"], y=df_trend["개인순매수"], marker_color="#aaaaaa"))
            fig2.update_layout(
                title=f"{h['name']} 투자자 동향",
                barmode="group",
                paper_bgcolor="#0e1117",
                plot_bgcolor="#0e1117",
                font=dict(color="#ffffff"),
                legend=dict(orientation="h", y=-0.3),
                margin=dict(l=10, r=10, t=40, b=60),
                height=260
            )
            st.plotly_chart(fig2, use_container_width=True)

st.divider()
st.markdown('<div class="section-title">🧮 매매 의사결정 도구</div>', unsafe_allow_html=True)
tab1, tab2 = st.tabs(["📥 매수 계산기 (2% 룰)", "🚨 매도 신호등"])

with tab1:
    st.markdown("**최적 매수 주수 계산 (총자산의 2% 리스크 기준)**")
    bc1, bc2 = st.columns(2)
    with bc1:
        b_ticker = st.text_input("종목 코드", value="005930", key="buy_ticker")
        b_price_input = st.number_input("현재가 (원)", min_value=1, value=70000, step=100, key="buy_price")
        if st.button("🔍 현재가 자동조회", use_container_width=True):
            fetched = fetch_stock_price(b_ticker, APP_KEY, APP_SECRET)
            if fetched:
                b_price_input = fetched["price"]
                st.success(f"현재가: {fetched['price']:,.0f}원")
            else:
                st.error("조회 실패")
    with bc2:
        b_stop = st.number_input("손절가 (원)", min_value=1, value=63000, step=100, key="buy_stop")
        b_risk = st.slider("리스크 비율 (%)", 1, 5, 2, key="buy_risk")

    total_assets_calc = total_eval + st.session_state.cash_balance if st.session_state.holdings else st.session_state.cash_balance
    opt_qty = calc_position_size(total_assets_calc, b_price_input, b_stop, b_risk / 100)
    expected_loss = opt_qty * (b_price_input - b_stop)
    target_price = b_price_input * 1.1

    r1, r2, r3 = st.columns(3)
    r1.metric("최적 매수 주수", f"{opt_qty:,}주")
    r2.metric("예상 최대 손실", f"{expected_loss:,.0f}원")
    r3.metric("목표가 (+10%)", f"{target_price:,.0f}원")

with tab2:
    if not st.session_state.holdings:
        st.info("포트폴리오에 종목을 추가해 주세요.")
    else:
        for h in st.session_state.holdings:
            stock_d = fetch_stock_price(h["ticker"], APP_KEY, APP_SECRET)
            cur_p = stock_d["price"] if stock_d else 0.0
            s_ret = calc_stock_return(cur_p, h["avg_price"])
            k_ret = calc_kospi_return(kospi_val, h["buy_kospi"])
            g = calc_relative_signal(s_ret, k_ret)
            sig, cls = get_signal_status(g)
            add_ok = can_add_buy(s_ret, stock_d["ma20"] if stock_d else 0)

            with st.expander(f"{h['name']} ({h['ticker']}) — {sig}", expanded=(cls == "critical")):
                sc1, sc2, sc3 = st.columns(3)
                sc1.metric("현재가", f"{cur_p:,.0f}원")
                sc2.metric("상대 Gap", f"{g:.2f}%")
                sc3.metric("신호", sig)
                if cls == "critical":
                    st.error("🚨 즉시 매도를 검토하세요!")
                elif cls == "warning":
                    st.warning("⚠️ 주의 구간입니다. 모니터링을 강화하세요.")
                else:
                    st.success("✅ 정상 구간입니다.")
                st.caption(f"불타기 추가매수: {'✅ 가능' if add_ok else '❌ 불가'}")

st.divider()
st.markdown("""
<div style='text-align:center;color:#555;font-size:0.75rem'>
🛡️ 리스크 관리 네비게이터 | 데이터 출처: 
<a href='https://apiportal.koreainvestment.com' style='color:#4a9eff'>KIS OpenAPI</a> | 
투자 판단의 최종 책임은 본인에게 있습니다.
</div>
""", unsafe_allow_html=True)

if auto_refresh:
    time.sleep(60)
    st.rerun()
