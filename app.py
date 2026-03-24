import streamlit as st
import pandas as pd
import numpy as np
import math
from gurobipy import Model, GRB, quicksum
from datetime import datetime, timedelta


# =================================================================
# 第一部分：纯净的数学与算法引擎 (彻底剥离了 GUI)
# =================================================================
class OptimizationEngine:
    def calculate_annualized_cost(self, initial_invest, om_cost, life, overhaul_cycle, overhaul_cost, discount_rate,
                                  residual_rate=0, project_life=25):
        if discount_rate == 0:
            crf = 1 / project_life
        else:
            crf = (discount_rate * (1 + discount_rate) ** project_life) / ((1 + discount_rate) ** project_life - 1)

        total_npv = initial_invest
        if discount_rate == 0:
            npv_om = om_cost * project_life
        else:
            npv_om = om_cost * (((1 + discount_rate) ** project_life - 1) / (
                    discount_rate * (1 + discount_rate) ** project_life))
        total_npv += npv_om

        for year in range(1, project_life):
            if year % life == 0:
                total_npv += initial_invest / ((1 + discount_rate) ** year)
            elif overhaul_cycle > 0 and (year % life) % overhaul_cycle == 0:
                total_npv += overhaul_cost / ((1 + discount_rate) ** year)

        last_replace_year = (project_life - 1) // life * life
        used_years = project_life - last_replace_year
        remaining_life_ratio = max(0, (life - used_years) / life)
        real_residual_value = initial_invest * remaining_life_ratio + initial_invest * residual_rate

        if real_residual_value > 0:
            npv_residual = real_residual_value / ((1 + discount_rate) ** project_life)
            total_npv -= npv_residual

        return total_npv * crf

    def parse_efficiency_curve(self, load_str, spec_energy_str, unit_cap):
        try:
            loads = [float(x.strip()) / 100 for x in load_str.split(',')]
            specs = [float(x.strip()) for x in spec_energy_str.split(',')]
            if len(loads) != len(specs): return None, "负荷点与单耗点数量不一致"
            points = []
            for l, s in zip(loads, specs):
                p_val = l * unit_cap
                if s > 0:
                    m_val = p_val / s
                    points.append((m_val, p_val))
            points.sort(key=lambda x: x[0])
            lines = []
            for i in range(len(points) - 1):
                m1, p1 = points[i]
                m2, p2 = points[i + 1]
                if m2 - m1 == 0: continue
                k = (p2 - p1) / (m2 - m1)
                b = p1 - k * m1
                lines.append({'k': k, 'b': b})
            return lines, None
        except ValueError:
            return None, "效率曲线格式错误，请使用英文逗号分隔"

    def execute_optimization(self, params):
        try:
            # 兼容 Streamlit 的文件流读取机制
            if hasattr(params['res_file'], 'seek'):
                params['res_file'].seek(0)
            wind_solar_data = pd.read_csv(params['res_file'])

            pv_pu = wind_solar_data.iloc[:, 0].values * params['pv_cf_factor']
            wind_pu = wind_solar_data.iloc[:, 1].values * params['wind_cf_factor']
            T = 8760

            h2_demand = np.zeros(T)
            if params['h2_mode'] == 'LOAD':
                if params['h2_demand_source'] == 'FILE' and params['h2_file'] is not None:
                    if hasattr(params['h2_file'], 'seek'):
                        params['h2_file'].seek(0)
                    h2_data = pd.read_csv(params['h2_file'])
                    h2_demand = h2_data.iloc[:, 0].values
                else:
                    avg_load = params['h2_target_annual'] / 8760
                    h2_demand = np.full(T, avg_load)

            model = Model("HydrogenSystemDesign")
            model.setParam('OutputFlag', 0)
            model.setParam('MIPGap', params['mip_gap'])
            model.setParam('TimeLimit', params['time_limit'])

            n_wind = model.addVar(vtype=GRB.INTEGER, lb=0, name="n_wind")
            n_pv = model.addVar(vtype=GRB.INTEGER, lb=0, name="n_pv")
            n_bat = model.addVar(vtype=GRB.INTEGER, lb=0, name="n_bat")
            n_elec = model.addVar(vtype=GRB.INTEGER, lb=0, name="n_elec")
            n_tank = model.addVar(vtype=GRB.INTEGER, lb=0, name="n_tank")

            p_wind_used = model.addVars(T, lb=0)
            p_pv_used = model.addVars(T, lb=0)
            p_curtail = model.addVars(T, lb=0)
            p_bat_ch = model.addVars(T, lb=0)
            p_bat_dis = model.addVars(T, lb=0)
            e_bat = model.addVars(T, lb=0)
            p_grid_imp = model.addVars(T, lb=0)
            p_elec = model.addVars(T, lb=0, name="p_elec_total")
            n_elec_on = model.addVars(T, vtype=GRB.INTEGER, lb=0, name="n_elec_on")
            m_h2_prod = model.addVars(T, lb=0, name="h2_prod")
            h2_shortage = model.addVars(T, lb=0, name="h2_shortage")
            m_h2_offtake = model.addVars(T, lb=0, name="h2_offtake")
            m_tank_in = model.addVars(T, lb=0)
            m_tank_out = model.addVars(T, lb=0)
            m_tank_level = model.addVars(T, lb=0)

            cost_wind_annual = self.calculate_annualized_cost(
                params['wind_cost'], params['wind_om'], params['wind_life'],
                params['wind_overhaul_cycle'], params['wind_overhaul_cost'],
                params['discount_rate'], params['residual_rate'], params['project_life'])
            cost_pv_annual = self.calculate_annualized_cost(
                params['pv_cost'], params['pv_om'], params['pv_life'],
                params['pv_overhaul_cycle'], params['pv_overhaul_cost'],
                params['discount_rate'], params['residual_rate'], params['project_life'])
            cost_bat_annual = self.calculate_annualized_cost(
                params['bat_cost_total'], params['bat_om_total'], params['bat_life'],
                params['bat_overhaul_cycle'], params['bat_overhaul_cost_total'],
                params['discount_rate'], params['residual_rate'], params['project_life'])
            cost_elec_annual = self.calculate_annualized_cost(
                params['elec_cost'], params['elec_om'], params['elec_life'],
                params['elec_overhaul_cycle'], params['elec_overhaul_cost'],
                params['discount_rate'], params['residual_rate'], params['project_life'])
            cost_tank_annual = self.calculate_annualized_cost(
                params['tank_cost'], 0, params['tank_life'],
                params['tank_overhaul_cycle'], params['tank_overhaul_cost'],
                params['discount_rate'], params['residual_rate'], params['project_life'])

            if params['wind_max_cap'] > 0: model.addConstr(n_wind * params['wind_unit_cap'] <= params['wind_max_cap'])
            if params['pv_max_cap'] > 0: model.addConstr(n_pv * params['pv_unit_cap'] <= params['pv_max_cap'])
            if params['elec_max_cap'] > 0: model.addConstr(n_elec * params['elec_unit_cap'] <= params['elec_max_cap'])
            if params['bat_max_cap'] > 0: model.addConstr(n_bat * params['bat_unit_energy'] <= params['bat_max_cap'])
            if params['tank_max_vol'] > 0: model.addConstr(n_tank * params['tank_unit_vol'] <= params['tank_max_vol'])

            max_h2_flow_per_hour = params['elec_max_cap'] * params['elec_min_eff_slope']
            max_h2_flow = max_h2_flow_per_hour * 1.5 if max_h2_flow_per_hour > 0 else 100000
            curve_lines = params['elec_curve_lines']

            for t in range(T):
                wind_avail = wind_pu[t] * n_wind * params['wind_unit_cap']
                pv_avail = pv_pu[t] * n_pv * params['pv_unit_cap']
                model.addConstr(p_wind_used[t] <= wind_avail)
                model.addConstr(p_pv_used[t] <= pv_avail)
                model.addConstr(p_curtail[t] == (wind_avail - p_wind_used[t]) + (pv_avail - p_pv_used[t]))

                model.addConstr(p_wind_used[t] + p_pv_used[t] + p_bat_dis[t] + p_grid_imp[t] ==
                                p_elec[t] + p_bat_ch[t] + m_tank_in[t] * params['comp_spec_energy'])

                bat_e_cap = n_bat * params['bat_unit_energy']
                bat_p_cap = n_bat * params['bat_unit_power']

                model.addConstr(e_bat[t] <= bat_e_cap)
                model.addConstr(p_bat_ch[t] <= bat_p_cap)
                model.addConstr(p_bat_dis[t] <= bat_p_cap)

                if t == 0:
                    e_init = bat_e_cap * params['bat_init_soc']
                    model.addConstr(
                        e_bat[t] == e_init + p_bat_ch[t] * params['bat_eff_ch'] - p_bat_dis[t] / params['bat_eff_dis'])
                else:
                    model.addConstr(
                        e_bat[t] == e_bat[t - 1] + p_bat_ch[t] * params['bat_eff_ch'] - p_bat_dis[t] / params[
                            'bat_eff_dis'])

                model.addConstr(n_elec_on[t] <= n_elec)
                model.addConstr(p_elec[t] <= n_elec_on[t] * params['elec_unit_cap'])
                model.addConstr(p_elec[t] >= n_elec_on[t] * params['elec_unit_cap'] * params['elec_min_load'])

                for i, line in enumerate(curve_lines):
                    k = line['k']
                    b = line['b']
                    model.addConstr(p_elec[t] >= k * m_h2_prod[t] + b * n_elec_on[t])

                model.addConstr(m_h2_prod[t] >= params['elec_min_eff_slope'] * p_elec[t])
                model.addConstr(m_h2_prod[t] + m_tank_out[t] == m_tank_in[t] + m_h2_offtake[t])

                if params['h2_mode'] == 'LOAD':
                    model.addConstr(m_h2_offtake[t] == h2_demand[t] - h2_shortage[t])
                else:
                    model.addConstr(h2_shortage[t] == 0)

                tank_cap = n_tank * params['tank_unit_vol']
                model.addConstr(m_tank_level[t] <= tank_cap)
                model.addConstr(m_tank_in[t] <= max_h2_flow)
                model.addConstr(m_tank_out[t] <= max_h2_flow)

                if t == 0:
                    tank_init = tank_cap * params['tank_init_soc']
                    model.addConstr(m_tank_level[t] == tank_init + m_tank_in[t] - m_tank_out[t])
                else:
                    model.addConstr(m_tank_level[t] == m_tank_level[t - 1] + m_tank_in[t] - m_tank_out[t])

                if not params['grid_connected']: model.addConstr(p_grid_imp[t] == 0)

            model.addConstr(e_bat[T - 1] >= n_bat * params['bat_unit_energy'] * params['bat_init_soc'])
            model.addConstr(m_tank_level[T - 1] >= n_tank * params['tank_unit_vol'] * params['tank_init_soc'])

            total_h2_prod = quicksum(m_h2_prod[t] for t in range(T))
            model.addConstr(total_h2_prod >= params['h2_target_annual'] * (1 - params['h2_shortage_rate']))
            model.addConstr(total_h2_prod <= params['h2_target_annual'] * (1 + params['h2_prod_margin']))

            capex_annual = (cost_wind_annual * n_wind + cost_pv_annual * n_pv +
                            cost_bat_annual * n_bat + cost_elec_annual * n_elec + cost_tank_annual * n_tank)
            grid_cost_annual = quicksum(p_grid_imp[t] * params['grid_price'] for t in range(T))

            penalty_cost = (quicksum(p_curtail[t] for t in range(T)) * params['penalty_curtail'] +
                            quicksum(p_bat_ch[t] + p_bat_dis[t] for t in range(T)) * params['penalty_bat_cyc'] +
                            quicksum(m_tank_in[t] for t in range(T)) * params['penalty_tank_in'])
            shortage_cost = quicksum(h2_shortage[t] for t in range(T)) * params['penalty_h2_shortage']

            model.setObjective(capex_annual + grid_cost_annual + penalty_cost + shortage_cost, GRB.MINIMIZE)
            model.optimize()

            if model.status == GRB.OPTIMAL or (model.status == GRB.TIME_LIMIT and model.SolCount > 0):
                final_gap = model.MIPGap
                res_wind_cap = n_wind.x * params['wind_unit_cap']
                res_pv_cap = n_pv.x * params['pv_unit_cap']
                res_bat_energy = n_bat.x * params['bat_unit_energy']
                res_elec_cap = n_elec.x * params['elec_unit_cap']
                res_tank_cap = n_tank.x * params['tank_unit_vol']
                time_index = [datetime(2026, 1, 1) + timedelta(hours=i) for i in range(T)]
                total_shortage = sum(h2_shortage[t].x for t in range(T))

                elec_num_on = np.round([n_elec_on[t].x for t in range(T)]).astype(int)
                elec_total_power = np.array([p_elec[t].x for t in range(T)])
                elec_unit_load = np.zeros(T)
                mask = elec_num_on > 0.5
                elec_unit_load[mask] = elec_total_power[mask] / elec_num_on[mask]

                full_year_data = pd.DataFrame({
                    'Time': time_index,
                    'TimeStr': [t.strftime('%Y-%m-%d %H:00') for t in time_index],
                    'Hour': range(1, 8761),
                    'H2_Demand': h2_demand,
                    'H2_Offtake': [m_h2_offtake[t].x for t in range(T)],
                    'H2_Shortage': [h2_shortage[t].x for t in range(T)],
                    'H2_Prod': [m_h2_prod[t].x for t in range(T)],
                    'Tank_Out': [m_tank_out[t].x for t in range(T)],
                    'Tank_In': [m_tank_in[t].x for t in range(T)],
                    'Tank_Level': [m_tank_level[t].x for t in range(T)],
                    'Wind_Gen': [wind_pu[t] * res_wind_cap for t in range(T)],
                    'Wind_Used': [p_wind_used[t].x for t in range(T)],
                    'PV_Gen': [pv_pu[t] * res_pv_cap for t in range(T)],
                    'PV_Used': [p_pv_used[t].x for t in range(T)],
                    'Total_RE': [(wind_pu[t] * res_wind_cap + pv_pu[t] * res_pv_cap) for t in range(T)],
                    'Curtail': [p_curtail[t].x for t in range(T)],
                    'Grid_Imp': [p_grid_imp[t].x for t in range(T)],
                    'Load_Elec': elec_total_power,
                    'Load_Comp': [m_tank_in[t].x * params['comp_spec_energy'] for t in range(T)],
                    'Bat_Ch': [p_bat_ch[t].x for t in range(T)],
                    'Bat_Dis': [p_bat_dis[t].x for t in range(T)],
                    'Bat_Level': [e_bat[t].x for t in range(T)],
                    'Elec_Num_On': elec_num_on,
                    'Elec_Unit_Load': elec_unit_load
                })

                # 年化成本结算
                costs_annual = {
                    'Wind': cost_wind_annual * n_wind.x, 'PV': cost_pv_annual * n_pv.x,
                    'Battery': cost_bat_annual * n_bat.x, 'Electrolyzer': cost_elec_annual * n_elec.x,
                    'Tank': cost_tank_annual * n_tank.x, 'Grid': grid_cost_annual.getValue()
                }

                total_offtake = sum(m_h2_offtake[t].x for t in range(T))
                if total_offtake < 1: total_offtake = 1

                return {
                    'success': True,
                    'metrics': {
                        'lcoh': (capex_annual.getValue() + grid_cost_annual.getValue()) / total_offtake,
                        'total_h2': sum(m_h2_prod[t].x for t in range(T)),
                        'shortage': total_shortage,
                        'grid_imp': sum(p_grid_imp[t].x for t in range(T)),
                        'annual_cost': capex_annual.getValue() + grid_cost_annual.getValue(),
                        'gap': final_gap
                    },
                    'full_year_data': full_year_data,
                    'costs': costs_annual
                }
            else:
                return {'success': False, 'message': f'求解失败，状态码: {model.status} (可能是容量上限过低无解)'}
        except Exception as e:
            return {'success': False, 'message': str(e)}


# =================================================================
# 第二部分：Streamlit 现代 Web UI 外壳 (交互层)
# =================================================================
def main():
    st.set_page_config(page_title="绿氢系统容量寻优 Pro", layout="wide", page_icon="⚡")
    st.title("⚡ 风光制氢系统容量寻优平台 (Web版)")
    st.markdown("---")

    # ========= 侧边栏：收集所有 40 个核心参数 =========
    with st.sidebar:
        st.header("⚙️ 全局参数配置")

        with st.expander("1. 项目与电网参数", expanded=True):
            project_life = st.number_input("项目周期 (年)", value=25)
            discount_rate = st.number_input("折现率 (%)", value=8.0)
            residual_rate = st.number_input("残值率 (%)", value=4.0)
            grid_connected = st.checkbox("允许并网购电", value=True)
            grid_price = st.number_input("购电电价 (元/kWh)", value=0.5)

        with st.expander("2. 风电参数"):
            wind_unit_cap = st.number_input("风电-单机(kW)", value=5000.0)
            wind_max_cap = st.number_input("风电-上限(kW)", value=1000000.0)
            wind_cost = st.number_input("风电-投资(元/kW)", value=5500.0)
            wind_om = st.number_input("风电-运维(元/kW)", value=30.0)
            wind_life = st.number_input("风电-寿命(年)", value=20)
            wind_deg = st.number_input("风电-年衰减率(%)", value=0.1)
            wind_cf_factor = st.number_input("风电-出力修正系数", value=1.0)

        with st.expander("3. 光伏参数"):
            pv_unit_cap = st.number_input("光伏-单机(kW)", value=500.0)
            pv_max_cap = st.number_input("光伏-上限(kW)", value=1000000.0)
            pv_cost = st.number_input("光伏-投资(元/kW)", value=3800.0)
            pv_om = st.number_input("光伏-运维(元/kW)", value=20.0)
            pv_life = st.number_input("光伏-寿命(年)", value=25)
            pv_deg = st.number_input("光伏-年衰减率(%)", value=0.5)
            pv_cf_factor = st.number_input("光伏-出力修正系数", value=1.0)

        with st.expander("4. 储能(电池)参数"):
            bat_unit_energy = st.number_input("电池-单机容量(kWh)", value=3000.0)
            bat_unit_power = st.number_input("电池-单机功率(kW)", value=1000.0)
            bat_max_cap = st.number_input("电池-上限(kWh)", value=5000000.0)
            bat_cost = st.number_input("电池-投资(元/kWh)", value=1800.0)
            bat_om = st.number_input("电池-运维(元/kWh)", value=40.0)
            bat_life = st.number_input("电池-寿命(年)", value=10)
            bat_deg = st.number_input("电池-年衰减率(%)", value=2.0)
            bat_eff_ch = st.number_input("电池-充效率(%)", value=95.0)
            bat_eff_dis = st.number_input("电池-放效率(%)", value=95.0)
            bat_init_soc = st.number_input("电池-初始SOC(%)", value=50.0)

        with st.expander("5. 电解槽参数"):
            elec_unit_cap = st.number_input("电解-单机(kW)", value=1000.0)
            elec_max_cap = st.number_input("电解-上限(kW)", value=5000000.0)
            elec_cost = st.number_input("电解-投资(元/kW)", value=4000.0)
            elec_om = st.number_input("电解-运维(元/kW)", value=145.0)
            elec_life = st.number_input("电解-寿命(年)", value=15)
            elec_deg = st.number_input("电解-年衰减率(%)", value=1.5)
            elec_min_load = st.number_input("电解-最低负荷(%)", value=20.0)
            elec_load_pts = st.text_input("效率曲线-负荷点(%)", "20, 40, 60, 80, 100")
            elec_spec_pts = st.text_input("效率曲线-单耗(kWh/kg)", "65, 55, 52, 54, 56")

        with st.expander("6. 储氢参数"):
            tank_unit_vol = st.number_input("储罐-单罐(kg)", value=500.0)
            tank_max_vol = st.number_input("储罐-上限(kg)", value=500000.0)
            tank_cost = st.number_input("储罐-投资(元/kg)", value=3000.0)
            tank_life = st.number_input("储罐-寿命(年)", value=20)
            tank_init_soc = st.number_input("储罐-初始SOC(%)", value=10.0)
            pres_in = st.number_input("进气压力(MPa)", value=1.5)
            pres_out = st.number_input("储氢压力(MPa)", value=20.0)
            comp_eff = st.number_input("压缩机等熵效率(%)", value=70.0)

        with st.expander("7. 需求与防套利惩罚 (核心)"):
            h2_mode_str = st.radio("生产模式", ["负荷跟随 (LOAD)", "柔性生产 (FLEX)"])
            h2_target = st.number_input("年产氢目标 (kg)", value=500000.0)
            h2_shortage_rate = st.number_input("允许缺氢率 (%)", value=5.0)
            h2_prod_margin = st.number_input("产氢上限裕量 (%)", value=10.0)
            st.markdown("---")
            # 按照我们推演的“黄金防套利法则”设置的默认值
            penalty_curtail = st.number_input("弃电惩罚", value=0.001, format="%.4f")
            penalty_bat = st.number_input("充放电惩罚", value=0.01)
            penalty_tank = st.number_input("充氢惩罚", value=0.01)
            penalty_h2 = st.number_input("缺氢天价惩罚", value=100.0)

        with st.expander("8. 算法控制"):
            mip_gap = st.number_input("MIP Gap (%)", value=1.0)
            time_limit = st.number_input("最大求解时间 (s)", value=600)

        st.markdown("### 📁 数据文件导入")
        res_file = st.file_uploader("上传风光资源数据 (CSV)", type=['csv'])

        run_btn = st.button("🚀 开始极速寻优计算", type="primary", use_container_width=True)

    # ========= 主计算逻辑与图表展示 =========
    if run_btn:
        if res_file is None:
            st.error("⚠️ 请先在左侧上传包含 8760 小时的风光资源 CSV 文件！")
            return

        engine = OptimizationEngine()

        # 1. 预处理效率曲线
        curve_lines, err = engine.parse_efficiency_curve(elec_load_pts, elec_spec_pts, elec_unit_cap)
        if err:
            st.error(err)
            return

        # 2. 热力学压缩功耗计算
        if pres_out <= pres_in:
            comp_spec_energy = 0.0
        else:
            N_stages = max(1, math.ceil(math.log(pres_out / pres_in) / math.log(3)))
            w_kj_kg = N_stages * (1.4 / 0.4) * 4.124 * 293.15 * (
                        math.pow(pres_out / pres_in, 0.4 / (N_stages * 1.4)) - 1)
            comp_spec_energy = (w_kj_kg / (comp_eff / 100.0)) / 3600.0

        # 3. 组装终极 Params 字典 (严丝合缝对应引擎需求)
        params = {
            'project_life': int(project_life), 'discount_rate': discount_rate / 100,
            'residual_rate': residual_rate / 100,
            'wind_unit_cap': wind_unit_cap, 'wind_max_cap': wind_max_cap, 'wind_cost': wind_cost * wind_unit_cap,
            'wind_om': wind_om * wind_unit_cap, 'wind_life': int(wind_life), 'wind_deg': wind_deg / 100,
            'wind_cf_factor': wind_cf_factor, 'wind_overhaul_cycle': 0, 'wind_overhaul_cost': 0,
            'pv_unit_cap': pv_unit_cap, 'pv_max_cap': pv_max_cap, 'pv_cost': pv_cost * pv_unit_cap,
            'pv_om': pv_om * pv_unit_cap, 'pv_life': int(pv_life), 'pv_deg': pv_deg / 100, 'pv_cf_factor': pv_cf_factor,
            'pv_overhaul_cycle': 0, 'pv_overhaul_cost': 0,
            'bat_unit_energy': bat_unit_energy, 'bat_unit_power': bat_unit_power, 'bat_max_cap': bat_max_cap,
            'bat_cost_total': bat_cost * bat_unit_energy, 'bat_om_total': bat_om * bat_unit_energy,
            'bat_life': int(bat_life), 'bat_deg': bat_deg / 100, 'bat_eff_ch': bat_eff_ch / 100,
            'bat_eff_dis': bat_eff_dis / 100, 'bat_init_soc': bat_init_soc / 100, 'bat_overhaul_cycle': 0,
            'bat_overhaul_cost_total': 0,
            'elec_unit_cap': elec_unit_cap, 'elec_max_cap': elec_max_cap, 'elec_cost': elec_cost * elec_unit_cap,
            'elec_om': elec_om * elec_unit_cap, 'elec_life': int(elec_life), 'elec_deg': elec_deg / 100,
            'elec_min_load': elec_min_load / 100, 'elec_curve_lines': curve_lines,
            'elec_min_eff_slope': 1.0 / max([float(x.strip()) for x in elec_spec_pts.split(',')]),
            'elec_overhaul_cycle': 0, 'elec_overhaul_cost': 0,
            'tank_unit_vol': tank_unit_vol, 'tank_max_vol': tank_max_vol, 'tank_cost': tank_cost * tank_unit_vol,
            'tank_life': int(tank_life), 'tank_init_soc': tank_init_soc / 100, 'tank_overhaul_cycle': 0,
            'tank_overhaul_cost': 0,
            'comp_spec_energy': comp_spec_energy,
            'penalty_curtail': penalty_curtail, 'penalty_bat_cyc': penalty_bat, 'penalty_tank_in': penalty_tank,
            'penalty_h2_shortage': penalty_h2,
            'mip_gap': mip_gap / 100, 'time_limit': int(time_limit),
            'grid_connected': grid_connected, 'grid_price': grid_price,
            'h2_mode': 'LOAD' if 'LOAD' in h2_mode_str else 'FLEX',
            'h2_demand_source': 'CONST', 'h2_file': None,
            'h2_target_annual': h2_target, 'h2_shortage_rate': h2_shortage_rate / 100,
            'h2_prod_margin': h2_prod_margin / 100,
            'res_file': res_file
        }

        # 【魔法时刻】：极具科技感的加载动画，主界面绝不假死！
        with st.spinner('🧠 算法引擎 Gurobi 正在通过分支定界法穿梭高维空间，请稍候...'):
            try:
                result = engine.execute_optimization(params)

                if result['success']:
                    st.success("🎉 寻优计算圆满完成！(0-1变量剔除后速度起飞了吧！)")

                    # 1. 顶部核心数据看板 (大字报)
                    metrics = result['metrics']
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("LCOH 平准化氢成本", f"{metrics['lcoh']:.2f} 元/kg")
                    col2.metric("首年总制氢量", f"{metrics['total_h2'] / 1000:.1f} 吨")
                    col3.metric("全年购电量", f"{metrics['grid_imp'] / 10000:.1f} 万度")
                    col4.metric("MILP 求解 Gap", f"{metrics['gap'] * 100:.2f} %")

                    # 2. 成本占比图
                    st.markdown("### 💰 系统年化成本构成")
                    costs_df = pd.DataFrame(list(result['costs'].items()), columns=['Component', 'Cost_RMB'])
                    st.bar_chart(costs_df.set_index('Component'))

                    # 3. 极其丝滑的可交互动态曲线图
                    st.markdown("### 📈 首年逐时功率平衡分析 (鼠标悬停查看数值)")
                    df = result['full_year_data']
                    # 只提取用户最关心的几根功率线做展示
                    chart_data = df[['TimeStr', 'Load_Elec', 'Wind_Used', 'PV_Used']].set_index('TimeStr')
                    st.line_chart(chart_data)

                    st.markdown("### 📅 全年底层运行数据 (可导出)")
                    st.dataframe(df, use_container_width=True)

                else:
                    st.error(f"🛑 优化失败：{result['message']}")

            except Exception as e:
                st.error(f"发生代码级异常：{str(e)}")


if __name__ == "__main__":
    main()