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
c = conn.cursor()

# 初始化 PostgreSQL 数据表结构
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
conn.commit() 

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
                
                st.write("📌 **第一步：设定计量单位**")
                u_col1, u_col2 = st.columns(2)
                with u_col1:
                    active_unit = st.text_input("日常计量单位", placeholder="例如: 个, 袋, 盒", value="个")
                with u_col2:
                    active_weight_per_unit = st.number_input(f"每一【{active_unit}】大约多少克(g)?", min_value=0.1, value=50.0)
                
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
                
                f_cal = r_cal / 4.184 if "kJ" in e_unit else r_cal
                cal_pg, pro_pg, fat_pg, carb_pg, fiber_pg = [x/base_w for x in [f_cal, r_pro, r_fat, r_carb, r_fiber]]
            else:
                active_unit, active_weight_per_unit = f_data[1], f_data[2]
                cal_pg, pro_pg, fat_pg, carb_pg, fiber_pg = f_data[3], f_data[4], f_data[5], f_data[6], f_data[7]
                st.success(f"✅ 已选中：【{food_name}】 (1 {active_unit} = {active_weight_per_unit}g)")

            st.divider()
            st.write("⚖️ **第三步：确认本次摄入量**")
            
            input_mode = st.radio("请选择计量方式：", [f"按【{active_unit}】数量输入", "直接输入【克(g)】重量"], horizontal=True)
            
            if "克" in input_mode:
                total_weight = st.number_input("🤔 你这次吃了多少克 (g)?", min_value=0.1, value=float(active_weight_per_unit), step=10.0)
                quantity_input = total_weight / active_weight_per_unit
                st.caption(f"系统自动折算为: {quantity_input:.2f} {active_unit}")
            else:
                quantity_input = st.number_input(f"🤔 你这次吃了多少【{active_unit}】?", min_value=0.1, value=1.0, step=0.5)
                total_weight = quantity_input * active_weight_per_unit
                st.caption(f"系统自动折算总重量为: {total_weight:.1f} g")

            if st.button("确认添加记录", use_container_width=True, type="primary"):
                if not f_data and food_name:
                    c.execute("INSERT INTO food_lib_v2 VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", 
                              (food_name, active_unit, active_weight_per_unit, cal_pg, pro_pg, fat_pg, carb_pg, fiber_pg))
                
                now_date = datetime.now().strftime("%Y-%m-%d")
                c.execute("INSERT INTO daily_log_v2 (date, name, quantity, unit_name, total_weight, cal, pro, fat, carb, fiber) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                          (now_date, food_name, quantity_input, active_unit, total_weight, 
                           cal_pg*total_weight, pro_pg*total_weight, fat_pg*total_weight, carb_pg*total_weight, fiber_pg*total_weight))
                conn.commit() 
                st.rerun()

    with col2:
        st.header("2. 今日摄入与目标达成率")
        today = datetime.now().strftime("%Y-%m-%d")
        today_df = pd.read_sql_query(f"SELECT * FROM daily_log_v2 WHERE date='{today}'", conn)
        
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
    tab_list, tab_history = st.tabs(["📋 今日摄入清单(可直接修改)", "📈 长期趋势统计(数据表)"])
    
    with tab_list:
        if not today_df.empty:
            st.write("💡 **使用提示**：你可以直接在下方表格中双击修改 **[数量]** 或 **[总重(g)]**。勾选最左侧并按键盘 `Delete` 键可以删除该行。修改完成后，点击最下方的【保存】按钮即可自动重算营养！")
            
            edit_df = today_df[['id', 'name', 'quantity', 'unit_name', 'total_weight', 'cal', 'pro', 'fat', 'carb', 'fiber']].copy()
            edit_df.columns = ['ID', '食物名称', '数量', '单位', '总重(g)', '热量', '蛋白', '脂肪', '碳水', '纤维']
            
            edited_df = st.data_editor(
                edit_df,
                disabled=['ID', '食物名称', '单位', '热量', '蛋白', '脂肪', '碳水', '纤维'],
                hide_index=True,
                num_rows="dynamic",
                use_container_width=True
            )
            
            if st.button("💾 保存对今日清单的修改", type="primary"):
                current_ids = edited_df['ID'].tolist()
                original_ids = edit_df['ID'].tolist()
                deleted_ids = [i for i in original_ids if i not in current_ids]
                for d_id in deleted_ids:
                    c.execute("DELETE FROM daily_log_v2 WHERE id=%s", (int(d_id),))
                
                for index, row in edited_df.iterrows():
                    orig_row = edit_df[edit_df['ID'] == row['ID']]
                    if not orig_row.empty:
                        old_qty = orig_row.iloc[0]['数量']
                        new_qty = row['数量']
                        old_w = orig_row.iloc[0]['总重(g)']
                        new_w = row['总重(g)']
                        
                        ratio = 1.0
                        if new_qty != old_qty and old_qty > 0:
                            ratio = new_qty / old_qty
                        elif new_w != old_w and old_w > 0:
                            ratio = new_w / old_w
                            
                        if ratio != 1.0:
                            log_id = int(row['ID'])
                            c.execute('''UPDATE daily_log_v2 
                                         SET quantity = quantity * %s, total_weight = total_weight * %s,
                                             cal = cal * %s, pro = pro * %s, fat = fat * %s,
                                             carb = carb * %s, fiber = fiber * %s
                                         WHERE id = %s''', 
                                      (ratio, ratio, ratio, ratio, ratio, ratio, ratio, log_id))
                conn.commit() 
                st.success("今日数据修改已成功同步并重新计算！")
                st.rerun()
        else:
            st.info("今日暂无记录。")

    # --- ✨ 修改 1：长期趋势统计改为表格形式 ---
    with tab_history:
        all_log = pd.read_sql_query("SELECT * FROM daily_log_v2", conn)
        if not all_log.empty:
            st.write("📊 **每日营养摄入统计汇总表**")
            # 按日期分组求和
            trend_df = all_log.groupby('date').sum(numeric_only=True).reset_index()
            # 提取需要的列并重命名
            display_trend = trend_df[['date', 'cal', 'pro', 'fat', 'carb', 'fiber']].copy()
            display_trend.columns = ['日期', '总热量(kcal)', '蛋白质(g)', '脂肪(g)', '碳水(g)', '膳食纤维(g)']
            # 格式化数字，保留一位小数
            display_trend = display_trend.round(1)
            # 倒序排列，把最新的日期排在最上面
            display_trend = display_trend.sort_values(by='日期', ascending=False)
            
            # 以表格形式展示
            st.dataframe(display_trend, use_container_width=True, hide_index=True)
        else:
            st.info("暂无历史记录。")

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
            conn.commit() 
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
    lib_df = pd.read_sql_query("SELECT * FROM food_lib_v2", conn)
    
    # 增加空状态提示，再也不会一片空白了
    if not lib_df.empty:
        st.write("💡 **重要提示：** 当您在此处修改某种食物的成分（如修改了每克热量、纤维等）并点击保存后，**系统会自动同步更新您所有的历史日记记录**，确保营养分析准确无误！")
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
        
        if st.button("💾 保存并同步更新所有历史记录", type="primary"):
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
            
            # --- ✨ 终极防丢写法：使用原生 SQL 逐行 Upsert (冲突则更新)，绝不粗暴删库 ---
            for _, row in final_save.iterrows():
                c.execute('''
                    INSERT INTO food_lib_v2 (name, unit_name, weight_per_unit, cal_per_g, pro_per_g, fat_per_g, carb_per_g, fiber_per_g)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (name) DO UPDATE SET
                        unit_name=EXCLUDED.unit_name,
                        weight_per_unit=EXCLUDED.weight_per_unit,
                        cal_per_g=EXCLUDED.cal_per_g,
                        pro_per_g=EXCLUDED.pro_per_g,
                        fat_per_g=EXCLUDED.fat_per_g,
                        carb_per_g=EXCLUDED.carb_per_g,
                        fiber_per_g=EXCLUDED.fiber_per_g
                ''', tuple(row))
            conn.commit()
            
            # 处理被用户在表格中按 Delete 键彻底删掉的食物
            existing_names = final_save['name'].tolist()
            if existing_names:
                old_names = lib_df['name'].tolist()
                deleted_names = [n for n in old_names if n not in existing_names]
                for dn in deleted_names:
                    c.execute("DELETE FROM food_lib_v2 WHERE name=%s", (dn,))
                conn.commit()

            # --- ✨ 级联更新逻辑：用最新食物库重新核算日记表 ---
            c.execute('''
                UPDATE daily_log_v2 d
                SET cal = d.total_weight * f.cal_per_g,
                    pro = d.total_weight * f.pro_per_g,
                    fat = d.total_weight * f.fat_per_g,
                    carb = d.total_weight * f.carb_per_g,
                    fiber = d.total_weight * f.fiber_per_g
                FROM food_lib_v2 f
                WHERE d.name = f.name
            ''')
            conn.commit()
            # ---------------------------------------------------------------------------------
            
            st.success("云端食物库已完美更新！所有历史饮食日记数据也已同步重新计算完毕！")
            st.rerun()
    else:
        # 这个提示能防止页面崩溃变白
        st.info("🍎 食物库目前是空的。请先去左侧『饮食记录与今日概览』页面随便添加一个新食物，这里就会恢复显示啦！")
