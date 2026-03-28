import streamlit as st
import pandas as pd
from sqlalchemy import create_engine
import psycopg2
from datetime import datetime, timedelta
import plotly.express as px

# --- 1. 云端数据库连接配置 ---
db_url = st.secrets["DB_URL"]
engine = create_engine(db_url)
conn = engine.raw_connection()
conn.autocommit = True
c = conn.cursor()

# 初始化 PostgreSQL 数据表结构 (升级为 v2 表，增加单位和单件重量字段)
c.execute('''CREATE TABLE IF NOT EXISTS food_lib_v2 
             (name TEXT PRIMARY KEY, unit_name TEXT, weight_per_unit REAL, 
              cal_per_g REAL, pro_per_g REAL, fat_per_g REAL, carb_per_g REAL, fiber_per_g REAL)''')
c.execute('''CREATE TABLE IF NOT EXISTS daily_log_v2 
             (id SERIAL PRIMARY KEY, date TEXT, name TEXT, quantity REAL, unit_name TEXT, total_weight REAL, 
              cal REAL, pro REAL, fat REAL, carb REAL, fiber REAL)''')
c.execute('''CREATE TABLE IF NOT EXISTS user_profile 
             (id INTEGER PRIMARY KEY CHECK (id = 1), gender TEXT, age INTEGER, 
              height REAL, weight REAL, activity TEXT, target_cal REAL, 
              target_pro REAL, target_fat REAL, target_carb REAL, target_fiber REAL)''')

# --- 2. 页面设置 ---
st.set_page_config(page_title="营养分析追踪器", layout="wide", page_icon="🥗")
st.title("🍎 个人营养追踪与供能分析系统")

st.markdown("""
    <style>
    [data-testid="stMetricValue"] { font-size: 22px !important; }
    </style>
    """, unsafe_allow_html=True)

menu = st.sidebar.radio("功能导航", ["饮食记录与今日概览", "个人目标设置 (TDEE计算)", "食物库管理(修改/查看)"])

c.execute("SELECT * FROM user_profile WHERE id=1")
profile = c.fetchone()

# --- 3. 功能模块：饮食记录与今日概览 ---
if menu == "饮食记录与今日概览":
    col1, col2 = st.columns([1, 1.4])

    with col1:
        st.header("1. 记录饮食")
        
        c.execute("SELECT name FROM food_lib_v2")
        existing_foods = [row[0] for row in c.fetchall()]
        
        options = ["➕ 手动录入新食物..."] + existing_foods
        selected_option = st.selectbox("选择食物 (支持直接打字搜索)", options)
        
        if selected_option == "➕ 手动录入新食物...":
            food_name = st.text_input("请输入新食物名称", placeholder="例如：水煮蛋")
        else:
            food_name = selected_option

        if food_name:
            c.execute("SELECT * FROM food_lib_v2 WHERE name=%s", (food_name,))
            f_data = c.fetchone()

            if not f_data:
                st.warning(f"正在录入新食物：【{food_name}】")
                
                # --- 新增：自定义单位模块 ---
                st.write("📌 **第一步：设定计量单位**")
                u_col1, u_col2 = st.columns(2)
                with u_col1:
                    custom_unit = st.text_input("日常计量单位", placeholder="例如: 个, 袋, 盒", value="个")
                with u_col2:
                    weight_per_unit = st.number_input(f"每一【{custom_unit}】大约多少克(g)?", min_value=0.1, value=50.0)
                
                st.write("📊 **第二步：录入成分表数据**")
                u1, u2 = st.columns(2)
                with u1:
                    base_w = st.number_input("成分表基准重量 (g)", value=100.0)
                    e_unit = st.selectbox("热量单位", ["kcal", "kJ"])
                with u2:
                    r_cal = st.number_input(f"热量 (每{base_w}g)", 0.0)
                    r_pro = st.number_input(f"蛋白质 (g)", 0.0)
                    r_fat = st.number_input(f"脂肪 (g)", 0.0)
                    r_carb = st.number_input(f"碳水 (g)", 0.0)
                    r_fiber = st.number_input(f"膳食纤维 (g)", 0.0)
                
                # 换算逻辑
                f_cal = r_cal / 4.184 if "kJ" in e_unit else r_cal
                cal_pg, pro_pg, fat_pg, carb_pg, fiber_pg = [x/base_w for x in [f_cal, r_pro, r_fat, r_carb, r_fiber]]
                
                st.divider()
                # 录入新食物时的摄入量
                quantity_input = st.number_input(f"🤔 你这次吃了多少【{custom_unit}】?", min_value=0.1, value=1.0)
                total_weight = quantity_input * weight_per_unit

            else:
                unit_name, weight_per_unit = f_data[1], f_data[2]
                cal_pg, pro_pg, fat_pg, carb_pg, fiber_pg = f_data[3], f_data[4], f_data[5], f_data[6], f_data[7]
                
                st.success(f"✅ 已选中：【{food_name}】 (1 {unit_name} = {weight_per_unit}g)")
                
                # 直接按单位输入数量
                quantity_input = st.number_input(f"🤔 你吃了多少【{unit_name}】?", min_value=0.1, value=1.0)
                total_weight = quantity_input * weight_per_unit
                st.caption(f"系统自动折算总重量为: {total_weight:.1f} g")

            if st.button("确认添加记录", use_container_width=True, type="primary"):
                # 如果是新食物，先存入库
                if not f_data and food_name:
                    c.execute("INSERT INTO food_lib_v2 VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", 
                              (food_name, custom_unit, weight_per_unit, cal_pg, pro_pg, fat_pg, carb_pg, fiber_pg))
                
                # 存入每日记录
                now_date = datetime.now().strftime("%Y-%m-%d")
                c.execute("INSERT INTO daily_log_v2 (date, name, quantity, unit_name, total_weight, cal, pro, fat, carb, fiber) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                          (now_date, food_name, quantity_input, custom_unit if not f_data else unit_name, total_weight, 
                           cal_pg*total_weight, pro_pg*total_weight, fat_pg*total_weight, carb_pg*total_weight, fiber_pg*total_weight))
                st.rerun()

    with col2:
        st.header("2. 今日摄入与目标达成率")
        today = datetime.now().strftime("%Y-%m-%d")
        today_df = pd.read_sql_query(f"SELECT * FROM daily_log_v2 WHERE date='{today}'", engine)
        
        if profile:
            target_cal, target_pro, target_fat, target_carb, target_fiber = profile[6], profile[7], profile[8], profile[9], profile[10]
        else:
            target_cal = target_pro = target_fat = target_carb = target_fiber = 1 
            st.warning("👈 请先在左侧菜单去『个人目标设置』里计算您的专属营养目标哦！")

        if not today_df.empty:
            totals = today_df[['cal', 'pro', 'fat', 'carb', 'fiber']].sum()
            
            m1, m2, m3, m4, m5 = st.columns([1.2, 1, 1, 1, 1])
            m1.metric("今日热量", f"{totals['cal']:.0f}/{target_cal:.0f}")
            m2.metric("蛋白质", f"{totals['pro']:.1f}/{target_pro:.0f}g")
            m3.metric("脂肪", f"{totals['fat']:.1f}/{target_fat:.0f}g")
            m4.metric("碳水", f"{totals['carb']:.1f}/{target_carb:.0f}g")
            m5.metric("纤维", f"{totals['fiber']:.1f}/{target_fiber:.0f}g")

            if profile:
                st.progress(min(totals['cal'] / target_cal, 1.0), text="🔥 热量摄入进度")
                st.progress(min(totals['pro'] / target_pro, 1.0), text="🥩 蛋白质摄入进度")
                st.progress(min(totals['fiber'] / target_fiber, 1.0), text="🥦 膳食纤维摄入进度")

            pro_energy, fat_energy, carb_energy = totals['pro'] * 4, totals['fat'] * 9, totals['carb'] * 4
            total_calc_e = pro_energy + fat_energy + carb_energy
            
            p_ratio = (pro_energy/total_calc_e)*100 if total_calc_e>0 else 0
            f_ratio = (fat_energy/total_calc_e)*100 if total_calc_e>0 else 0
            c_ratio = (carb_energy/total_calc_e)*100 if total_calc_e>0 else 0

            st.write("---")
            st.subheader("💡 供能比健康分析")
            s_col1, s_col2, s_col3 = st.columns(3)
            s_col1.write(f"**蛋白质供能: {p_ratio:.1f}%** (推荐:15-25%)")
            s_col2.write(f"**脂肪供能: {f_ratio:.1f}%** (推荐:20-30%)")
            s_col3.write(f"**碳水供能: {c_ratio:.1f}%** (推荐:45-65%)")
            
            fig = px.pie(values=[pro_energy, fat_energy, carb_energy], names=['蛋白质供能', '脂肪供能', '碳水供能'], hole=0.4)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("今天还没有记录哦！")

    st.divider()
    tab_list, tab_history = st.tabs(["📋 今日摄入清单", "📈 长期趋势统计"])
    with tab_list:
        if not today_df.empty:
            for _, row in today_df.iterrows():
                # 调整了列宽，让单位和重量显示得更清楚
                cols = st.columns([1.5, 1.5, 1, 1, 1, 1, 1, 0.5])
                cols[0].write(f"🍴 {row['name']}")
                # 显示：1 个 (50.0g)
                cols[1].write(f"{row['quantity']} {row['unit_name']} ({row['total_weight']}g)")
                cols[2].write(f"{row['cal']:.0f} kcal")
                cols[3].write(f"P: {row['pro']:.1f}g")
                cols[4].write(f"F: {row['fat']:.1f}g")
                cols[5].write(f"C: {row['carb']:.1f}g")
                cols[6].write(f"Fb: {row['fiber']:.1f}g")
                if cols[7].button("🗑️", key=f"del_{row['id']}"):
                    c.execute("DELETE FROM daily_log_v2 WHERE id=%s", (row['id'],))
                    st.rerun()
    with tab_history:
        all_log = pd.read_sql_query("SELECT * FROM daily_log_v2", engine)
        if not all_log.empty:
            st.plotly_chart(px.bar(all_log.groupby('date').sum(numeric_only=True).reset_index(), x='date', y='cal', title="热量波动趋势"), use_container_width=True)

# --- 4. 功能模块：个人目标设置 ---
elif menu == "个人目标设置 (TDEE计算)":
    st.header("🎯 每日营养目标测算")
    
    def_gender = profile[1] if profile else "女"
    def_age = profile[2] if profile else 30
    def_height = profile[3] if profile else 160.0
    def_weight = profile[4] if profile else 50.0
    def_activity = profile[5] if profile else "轻度活动 (每周运动1-3天)"

    with st.form("profile_form"):
        c1, c2 = st.columns(2)
        gender = c1.selectbox("性别", ["女", "男"], index=0 if def_gender=="女" else 1)
        age = c2.number_input("年龄 (岁)", min_value=10, max_value=100, value=def_age)
        height = c1.number_input("身高 (cm)", min_value=100.0, max_value=250.0, value=def_height)
        weight = c2.number_input("体重 (kg)", min_value=30.0, max_value=150.0, value=def_weight)
        
        activities = {
            "几乎不运动 (久坐)": 1.2,
            "轻度活动 (每周运动1-3天)": 1.375,
            "中度活动 (每周运动3-5天)": 1.55,
            "高度活动 (每周运动6-7天)": 1.725
        }
        activity = st.selectbox("日常活动水平", list(activities.keys()), index=list(activities.keys()).index(def_activity))
        
        if st.form_submit_button("计算并保存我的营养目标", type="primary"):
            bmr = (10 * weight + 6.25 * height - 5 * age + 5) if gender == "男" else (10 * weight + 6.25 * height - 5 * age - 161)
            tdee = bmr * activities[activity]
            
            t_pro, t_fat, t_carb = (tdee * 0.20) / 4, (tdee * 0.30) / 9, (tdee * 0.50) / 4
            t_fiber = (tdee / 1000) * 14 
            
            c.execute("SELECT id FROM user_profile WHERE id=1")
            if c.fetchone():
                c.execute('''UPDATE user_profile SET gender=%s, age=%s, height=%s, weight=%s, activity=%s, 
                             target_cal=%s, target_pro=%s, target_fat=%s, target_carb=%s, target_fiber=%s WHERE id=1''',
                          (gender, age, height, weight, activity, tdee, t_pro, t_fat, t_carb, t_fiber))
            else:
                c.execute('''INSERT INTO user_profile (id, gender, age, height, weight, activity, target_cal, target_pro, target_fat, target_carb, target_fiber) 
                             VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''', 
                          (gender, age, height, weight, activity, tdee, t_pro, t_fat, t_carb, t_fiber))
            st.balloons()
            st.success("目标已计算并成功保存至云端！")
            st.rerun()

    if profile:
        st.divider()
        st.subheader("📊 您的专属营养处方")
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("目标总热量", f"{profile[6]:.0f} kcal")
        m2.metric("目标蛋白质", f"{profile[7]:.0f} g")
        m3.metric("目标脂肪", f"{profile[8]:.0f} g")
        m4.metric("目标碳水", f"{profile[9]:.0f} g")
        m5.metric("目标膳食纤维", f"{profile[10]:.0f} g")

# --- 5. 食物库管理 ---
elif menu == "食物库管理(修改/查看)":
    st.header("📦 食物库管理")
    lib_df = pd.read_sql_query("SELECT * FROM food_lib_v2", engine)
    if not lib_df.empty:
        display_lib = lib_df.rename(columns={
            'name':'食物名称',
            'unit_name':'单位(如:个)',
            'weight_per_unit':'单件重量(g)',
            'cal_per_g':'每克热量',
            'pro_per_g':'每克蛋白',
            'fat_per_g':'每克脂肪',
            'carb_per_g':'每克碳水',
            'fiber_per_g':'每克膳食纤维'
        })
        edited_lib = st.data_editor(display_lib, num_rows="dynamic", use_container_width=True)
        if st.button("保存修改"):
            final_save = edited_lib.rename(columns={
                '食物名称':'name',
                '单位(如:个)':'unit_name',
                '单件重量(g)':'weight_per_unit',
                '每克热量':'cal_per_g',
                '每克蛋白':'pro_per_g',
                '每克脂肪':'fat_per_g',
                '每克碳水':'carb_per_g',
                '每克膳食纤维':'fiber_per_g'
            })
            c.execute("DELETE FROM food_lib_v2")
            final_save.to_sql('food_lib_v2', engine, if_exists='append', index=False)
            st.success("云端库更新成功！")
            st.rerun()
