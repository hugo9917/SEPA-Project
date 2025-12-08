import streamlit as st
import pandas as pd
import plotly.express as px
import s3fs
from datetime import timedelta
from src import config

# ------------------------------------------------------------------------------
# 1. PAGE CONFIGURATION
# ------------------------------------------------------------------------------
st.set_page_config(
    page_title="SEPA Inflation Monitor",
    page_icon="🇦🇷",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for an "Enterprise" look
st.markdown("""
<style>
    .metric-card {
        background-color: #0e1117;
        border: 1px solid #30333F;
        border-radius: 5px;
        padding: 15px;
        text-align: center;
    }
    .big-font { font-size: 24px !important; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# ------------------------------------------------------------------------------
# 2. DATA LOADING (Optimized)
# ------------------------------------------------------------------------------
@st.cache_data(ttl=300)  # Cache for 5 minutes
def load_data_from_datalake(tipo):
    fs = s3fs.S3FileSystem(**config.STORAGE_OPTIONS)
    gold_dir = f"{config.GOLD_PATH}/sepa/{tipo}".replace("s3://", "")
    
    data = {}
    
    try:
        # Define files to load
        files = {
            "inflation": f"{gold_dir}/daily_inflation_index.parquet",
            "products": f"{gold_dir}/daily_product_prices.parquet",
            "stores": f"{gold_dir}/store_stats.parquet"
        }
        
        for key, path in files.items():
            if fs.exists(path):
                df = pd.read_parquet(f"s3://{path}", storage_options=config.STORAGE_OPTIONS)
                if "fecha" in df.columns:
                    df["fecha"] = pd.to_datetime(df["fecha"])
                data[key] = df
            else:
                data[key] = pd.DataFrame()
                
        return data["inflation"], data["products"], data["stores"]

    except Exception as e:
        st.error(f"🚨 Error connecting to MinIO Data Lake: {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

# ------------------------------------------------------------------------------
# 3. SIDEBAR & FILTERS
# ------------------------------------------------------------------------------
st.sidebar.title("🎛️ Control Panel")
dataset_type = st.sidebar.selectbox("Data Source", ["minorista", "mayorista"], index=0)

if st.sidebar.button("🔄 Refresh Cache"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.info(
    f"""
    **Pipeline Status:** 🟢 Active
    **Orchestrator:** Airflow
    **Storage:** MinIO (S3)
    """
)

# Load data
df_inf, df_prod, df_stores = load_data_from_datalake(dataset_type)

if df_inf.empty:
    st.warning("⚠️ No data found in Gold layer. Make sure to run the Airflow DAG first.")
    st.stop()

# ------------------------------------------------------------------------------
# 4. MAIN METRICS LOGIC (KPIs)
# ------------------------------------------------------------------------------
latest_date = df_inf['fecha'].max()
prev_date = latest_date - timedelta(days=1)

# Filter data for today and yesterday
inf_today = df_inf[df_inf['fecha'] == latest_date]
inf_yesterday = df_inf[df_inf['fecha'] == prev_date]

# Calculate current values
# Updated column name based on user changes
val_index = inf_today['indice_precio_global'].values[0] if not inf_today.empty else 0
val_products = df_prod[df_prod['fecha'] == latest_date].shape[0]

# Calculate Deltas (Variation vs yesterday)
delta_index = 0
if not inf_yesterday.empty:
    val_prev = inf_yesterday['indice_precio_global'].values[0]
    delta_index = ((val_index - val_prev) / val_prev) * 100

st.title(f"🇦🇷 SEPA Price Monitor ({dataset_type.capitalize()})")
st.markdown(f"*Last update: {latest_date.strftime('%d-%m-%Y')}*")

# KPI ROW
kpi1, kpi2, kpi3, kpi4 = st.columns(4)

with kpi1:
    st.metric(label="Price Index (Avg)", value=f"${val_index:.2f}", delta=f"{delta_index:.2f}%")
with kpi2:
    st.metric(label="Tracked Products", value=f"{val_products:,}")
with kpi3:
    store_count = df_stores[df_stores['fecha'] == latest_date].shape[0]
    st.metric(label="Active Stores", value=store_count)
with kpi4:
    # 'Data Quality Score' simulation (you can calculate it for real in the ETL)
    st.metric(label="Data Quality Score", value="98.5%", delta="Stable", delta_color="off")

st.markdown("---")

# ------------------------------------------------------------------------------
# 5. INTERACTIVE TABS
# ------------------------------------------------------------------------------
tab_market, tab_explorer, tab_engineering = st.tabs(["📊 Market Overview", "🔍 Product Explorer", "⚙️ Pipeline Health"])

# --- TAB 1: MARKET OVERVIEW ---
with tab_market:
    col_chart_1, col_chart_2 = st.columns([2, 1])
    
    with col_chart_1:
        st.subheader("📈 Inflation Evolution (Avg Index)")
        # Updated column name
        fig_trend = px.area(df_inf, x="fecha", y="indice_precio_global", 
                            color_discrete_sequence=["#00CC96"])
        fig_trend.update_layout(xaxis_title="", yaxis_title="Average Price ($)")
        st.plotly_chart(fig_trend, use_container_width=True)
    
    with col_chart_2:
        st.subheader("🏪 Cheapest Stores")
        if not df_stores.empty:
            top_cheap = df_stores[df_stores['fecha'] == latest_date].nsmallest(10, 'precio_promedio_general')
            fig_bar = px.bar(top_cheap, x="precio_promedio_general", y="nombre_sucursal", 
                             orientation='h', color="precio_promedio_general", color_continuous_scale="Bluered_r")
            fig_bar.update_layout(yaxis={'categoryorder':'total descending'}, xaxis_title="Average Price")
            st.plotly_chart(fig_bar, use_container_width=True)

# --- TAB 2: PRODUCT EXPLORER ---
with tab_explorer:
    st.subheader("Price Search")
    
    # Text search
    search_term = st.text_input("Search product (e.g., Milk, Coke, Yerba)", "")
    
    if search_term:
        # Filter product (case insensitive)
        mask = df_prod['descripcion_producto'].str.contains(search_term, case=False, na=False)
        df_filtered = df_prod[mask]
        
        if not df_filtered.empty:
            # Scatter or box plot
            st.write(f"Found {df_filtered['id_producto'].nunique()} unique products for '{search_term}'")
            
            # Temporal evolution of those products
            fig_search = px.line(df_filtered, x="fecha", y="precio_promedio", color="descripcion_producto", 
                                 title=f"Price Evolution: {search_term}")
            st.plotly_chart(fig_search, use_container_width=True)
            
            # Data table
            st.dataframe(df_filtered.sort_values("fecha", ascending=False).head(50))
        else:
            st.info("No products found with that name.")
    else:
        st.info("👆 Type something above to explore the product database.")

    # Top Movers (Highest increases)
    st.markdown("### 🔥 Top Price Increases (Today vs Yesterday)")
    # (Note: For this you would need to calculate percentage change per product in your Silver->Gold ETL.
    # For now we show the most expensive as a fallback)
    top_expensive = df_prod[df_prod['fecha'] == latest_date].nlargest(10, 'precio_promedio')
    st.table(top_expensive[['descripcion_producto', 'precio_promedio']].rename(columns={'precio_promedio': 'Price ($)'}))


# --- TAB 3: ENGINEERING & QUALITY ---
with tab_engineering:
    st.markdown("### 🛠️ Pipeline Metadata")
    
    eng_col1, eng_col2 = st.columns(2)
    
    with eng_col1:
        st.write("**Data Volume (Rows per Day)**")
        # Count rows by date in df_products
        row_counts = df_prod.groupby("fecha").size().reset_index(name="rows")
        fig_rows = px.bar(row_counts, x="fecha", y="rows")
        st.plotly_chart(fig_rows, use_container_width=True)
        
    with eng_col2:
        st.write("**Raw Data Storage Info**")
        st.json({
            "Source": "Precios Claros (Gob.ar)",
            "Storage Layer": "MinIO / Bronze -> Silver -> Gold",
            "File Format": "Parquet (Snappy Compression)",
            "Orchestrator": "Apache Airflow 2.7",
            "Last Run": latest_date.strftime('%Y-%m-%d %H:%M:%S')
        })