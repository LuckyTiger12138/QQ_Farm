"""Web 服务模块 - FastAPI 提供网页截图预览与 Bot 控制"""
import io
import threading
import time
from typing import Callable
from loguru import logger

import cv2
import numpy as np
from PIL import Image as PILImage

# FastAPI 相关（延迟导入，避免未安装时报错）
_fastapi = None
_uvicorn = None

def _import_deps():
    global _fastapi, _uvicorn
    try:
        import fastapi as _fastapi
        import uvicorn as _uvicorn
    except ImportError:
        logger.warning("FastAPI/uvicorn 未安装，Web 服务不可用")
        return False
    return True


class WebServer:
    """Web 控制面板服务"""

    def __init__(self, host: str = "0.0.0.0", port: int = 8080, engine=None):
        self.host = host
        self.port = port
        self._engine_ref = engine  # 直接通过构造函数传入
        self._server: object | None = None
        self._thread: threading.Thread | None = None
        self._running = False

        # 由外部注入的回调/数据提供者
        self.get_bot_state: Callable = lambda: "stopped"       # 返回 running/paused/stopped
        self.get_stats: Callable = lambda: {}                   # 返回统计数据 dict
        self.start_bot: Callable = lambda: None
        self.stop_bot: Callable = lambda: None
        self.pause_bot: Callable = lambda: None
        self.resume_bot: Callable = lambda: None
        self.get_screenshot: Callable = lambda: None            # 返回 PIL Image 或 None

    def start(self):
        """在后台线程启动 Web 服务"""
        if self._running:
            return
        if not _import_deps():
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_server, daemon=True, name="web-server")
        self._thread.start()
        logger.info(f"Web 服务启动中: http://{self.host}:{self.port}")

    def stop(self):
        """停止 Web 服务"""
        logger.info(f"WebServer.stop() 被调用，_running={self._running}")
        logger.info(f"self._server: {self._server}")
        logger.info(f"self._thread: {self._thread}")
        
        if not self._running:
            logger.warning("Web 服务未在运行，无需停止")
            return
            
        self._running = False
        logger.info("设置 _running = False")

        # 通知 uvicorn 退出
        if self._server:
            logger.info("设置 self._server.should_exit = True")
            self._server.should_exit = True
            # 额外措施：关闭服务器的 socket
            try:
                if hasattr(self._server, 'servers') and self._server.servers:
                    for srv in self._server.servers:
                        srv.close()
                    logger.info("已关闭 uvicorn 服务器 sockets")
            except Exception as e:
                logger.warning(f"关闭服务器 sockets 失败: {e}")
        else:
            logger.warning("self._server 为 None")

        # 等待线程结束（最多等待 5 秒）
        if self._thread and self._thread.is_alive():
            logger.info("等待 Web 服务线程结束...")
            self._thread.join(timeout=5.0)
            # 如果线程仍未结束，强制终止
            if self._thread.is_alive():
                logger.warning("Web 服务线程未正常退出，强制终止")
                import ctypes
                try:
                    # 强制终止线程（最后手段）
                    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                        ctypes.c_long(self._thread.ident),
                        ctypes.py_object(SystemExit)
                    )
                    if res == 0:
                        logger.warning("强制终止失败：线程 ID 无效")
                    elif res > 1:
                        ctypes.pythonapi.PyThreadState_SetAsyncExc(
                            ctypes.c_long(self._thread.ident), None)
                        logger.warning("强制终止失败：多个异常")
                    else:
                        logger.info("已发送 SystemExit 到 Web 服务线程")
                except Exception as e:
                    logger.warning(f"强制终止 Web 服务线程失败: {e}")
                # 再等待一下
                time.sleep(1.0)
        else:
            logger.info("Web 服务线程未运行或已结束")
            
        # 重置状态
        self._server = None
        self._thread = None
        logger.info("Web 服务已完全停止")

    def _run_server(self):
        """在线程中运行 uvicorn"""
        try:
            from fastapi import FastAPI, Request
            from fastapi.responses import Response, HTMLResponse, JSONResponse
            import uvicorn
            import asyncio

            app = FastAPI(title="QQ Farm Bot", docs_url=None, redoc_url=None)

            # 添加中间件：检查服务是否仍在运行
            @app.middleware("http")
            async def check_running(request, call_next):
                if not self._running:
                    return HTMLResponse("<h1>Web 服务已停止</h1>", status_code=503)
                response = await call_next(request)
                return response

            # ── 页面 ──
            @app.get("/", response_class=HTMLResponse)
            async def index():
                return HTMLResponse(_PAGE_HTML)

            # ── API 
            STAT_LABELS = {
                "harvest": "收获", "plant": "播种", "water": "浇水",
                "weed": "除草", "bug": "除虫", "sell": "出售",
                "fertilize": "施肥", "steal": "偷菜",
                "help_water": "帮浇水", "help_weed": "帮除草",
                "help_bug": "帮除虫", "total_actions": "总操作", "rounds": "轮数",
                "elapsed": "已用时间", "next_farm_check": "下次农场巡查",
                "next_friend_check": "下次好友巡查", "state": "状态",
            }

            @app.get("/api/status")
            async def api_status():
                raw_stats = self.get_stats()
                # 统计 key 映射为中文
                stats = {}
                for k, v in raw_stats.items():
                    stats[STAT_LABELS.get(k, k)] = v

                next_farm = 0
                next_friend = 0
                config_info = {}

                # 直接使用 self._engine_ref
                try:
                    eng = getattr(self, '_engine_ref', None)
                    if eng:
                        sched = eng.scheduler
                        cfg = eng.config
                        next_farm = float(getattr(sched, '_next_farm_check', 0) or 0)
                        next_friend = float(getattr(sched, '_next_friend_check', 0) or 0)
                        fc = cfg.features.friend
                        sh = cfg.silent_hours
                        sc = cfg.schedule
                        feat = cfg.features
                        planting = cfg.planting
                        sell = cfg.sell
                        safety = cfg.safety
                        config_info = {
                            # 巡查间隔
                            "farm_check_seconds": sc.farm_check_seconds,
                            "friend_check_seconds": sc.friend_check_seconds,
                            # 种植设置
                            "player_level": planting.player_level,
                            "plant_strategy": planting.strategy.value,
                            "preferred_crop": planting.preferred_crop,
                            # 功能开关
                            "auto_harvest": feat.auto_harvest,
                            "auto_plant": feat.auto_plant,
                            "auto_fertilize": feat.auto_fertilize,
                            "auto_buy_seed": feat.auto_buy_seed,
                            "auto_water": feat.auto_water,
                            "auto_weed": feat.auto_weed,
                            "auto_bug": feat.auto_bug,
                            "auto_sell": feat.auto_sell,
                            "auto_task": feat.auto_task,
                            "auto_upgrade": feat.auto_upgrade,
                            # 好友操作
                            "friend_steal": fc.enable_steal,
                            "friend_weed": fc.enable_weed,
                            "friend_water": fc.enable_water,
                            "friend_bug": fc.enable_bug,
                            "max_steal_per_round": fc.max_steal_per_round,
                            # 出售策略
                            "sell_mode": sell.mode.value,
                            "sell_crops": sell.sell_crops,
                            # 安全设置
                            "run_mode": safety.run_mode.value,
                            "random_delay_min": safety.random_delay_min,
                            "random_delay_max": safety.random_delay_max,
                            "click_offset": safety.click_offset_range,
                            # 静默时段
                            "silent_enabled": sh.enabled,
                            "silent_start_hour": sh.start_hour,
                            "silent_start_minute": sh.start_minute,
                            "silent_end_hour": sh.end_hour,
                            "silent_end_minute": sh.end_minute,
                            # 其他
                            "window_keyword": cfg.window_title_keyword,
                        }
                except Exception:
                    pass

                # 如果引擎读取失败，尝试从 config.json 读取
                if not config_info:
                    try:
                        import os
                        import json
                        if os.path.exists("config.json"):
                            with open("config.json", "r", encoding="utf-8") as f:
                                d = json.load(f)
                            sc = d.get("schedule", {})
                            feat = d.get("features", {})
                            fc = feat.get("friend", {})
                            sh = d.get("silent_hours", {})
                            planting = d.get("planting", {})
                            sell = d.get("sell", {})
                            safety = d.get("safety", {})
                            config_info = {
                                "farm_check_seconds": sc.get("farm_check_seconds", 120),
                                "friend_check_seconds": sc.get("friend_check_seconds", 300),
                                "player_level": planting.get("player_level", 69),
                                "plant_strategy": planting.get("strategy", "preferred"),
                                "preferred_crop": planting.get("preferred_crop", "椰子"),
                                "auto_harvest": feat.get("auto_harvest", True),
                                "auto_plant": feat.get("auto_plant", True),
                                "auto_fertilize": feat.get("auto_fertilize", True),
                                "auto_buy_seed": feat.get("auto_buy_seed", True),
                                "auto_water": feat.get("auto_water", True),
                                "auto_weed": feat.get("auto_weed", True),
                                "auto_bug": feat.get("auto_bug", True),
                                "auto_sell": feat.get("auto_sell", False),
                                "auto_task": feat.get("auto_task", False),
                                "auto_upgrade": feat.get("auto_upgrade", False),
                                "friend_steal": fc.get("enable_steal", True),
                                "friend_weed": fc.get("enable_weed", True),
                                "friend_water": fc.get("enable_water", True),
                                "friend_bug": fc.get("enable_bug", True),
                                "max_steal_per_round": fc.get("max_steal_per_round", 0),
                                "sell_mode": sell.get("mode", "batch_all"),
                                "sell_crops": sell.get("sell_crops", []),
                                "run_mode": safety.get("run_mode", "background"),
                                "random_delay_min": safety.get("random_delay_min", 0.1),
                                "random_delay_max": safety.get("random_delay_max", 0.3),
                                "click_offset": safety.get("click_offset_range", 5),
                                "silent_enabled": sh.get("enabled", False),
                                "silent_start_hour": sh.get("start_hour", 2),
                                "silent_start_minute": sh.get("start_minute", 0),
                                "silent_end_hour": sh.get("end_hour", 6),
                                "silent_end_minute": sh.get("end_minute", 0),
                                "window_keyword": d.get("window_title_keyword", "QQ经典农场"),
                            }
                    except Exception:
                        pass

                return JSONResponse({
                    "state": self.get_bot_state(),
                    "stats": stats,
                    "next_farm_check": next_farm,
                    "next_friend_check": next_friend,
                    "config": config_info,
                })

            @app.get("/api/crops")
            async def api_crops():
                """获取作物列表"""
                try:
                    from models.game_data import CROPS
                    return JSONResponse([
                        {"name": c[0], "level": c[2], "grow_time": c[3], "exp": c[4]}
                        for c in CROPS
                    ])
                except Exception:
                    return JSONResponse([])

            @app.post("/api/config")
            async def api_config(request: Request):
                """保存配置"""
                try:
                    import json
                    # 使用 self._engine_ref（WebServer 实例的属性）
                    eng = getattr(self, '_engine_ref', None)
                    if not eng:
                        logger.warning("保存配置失败: 引擎未连接")
                        return JSONResponse({"ok": False, "msg": "引擎未连接"})

                    # 读取并解析请求体
                    raw = await request.body()
                    if not raw:
                        return JSONResponse({"ok": False, "msg": "请求体为空"})
                    try:
                        body = json.loads(raw)
                    except json.JSONDecodeError as e:
                        logger.warning(f"JSON 解析失败: {e}")
                        return JSONResponse({"ok": False, "msg": f"JSON 格式错误: {e}"})

                    if not isinstance(body, dict):
                        return JSONResponse({"ok": False, "msg": "请求体必须为 JSON 对象"})

                    logger.info(f"收到配置保存请求 ({len(body)} 个字段)")
                    cfg = eng.config

                    # 巡查间隔
                    if "farm_check_seconds" in body:
                        cfg.schedule.farm_check_seconds = int(body["farm_check_seconds"])
                    if "friend_check_seconds" in body:
                        cfg.schedule.friend_check_seconds = int(body["friend_check_seconds"])

                    # 种植设置
                    if "player_level" in body:
                        cfg.planting.player_level = int(body["player_level"])
                    if "plant_strategy" in body:
                        cfg.planting.strategy = body["plant_strategy"]
                    if "preferred_crop" in body:
                        cfg.planting.preferred_crop = body["preferred_crop"]

                    # 功能开关
                    for key in ["auto_harvest", "auto_plant", "auto_fertilize", "auto_buy_seed",
                                "auto_water", "auto_weed", "auto_bug", "auto_sell",
                                "auto_task", "auto_upgrade"]:
                        if key in body:
                            setattr(cfg.features, key, bool(body[key]))

                    # 好友操作
                    fc = cfg.features.friend
                    if "friend_steal" in body: fc.enable_steal = bool(body["friend_steal"])
                    if "friend_weed" in body: fc.enable_weed = bool(body["friend_weed"])
                    if "friend_water" in body: fc.enable_water = bool(body["friend_water"])
                    if "friend_bug" in body: fc.enable_bug = bool(body["friend_bug"])
                    if "max_steal_per_round" in body: fc.max_steal_per_round = int(body["max_steal_per_round"])

                    # 出售策略
                    if "sell_mode" in body:
                        cfg.sell.mode = body["sell_mode"]
                    if "sell_crops" in body:
                        cfg.sell.sell_crops = body["sell_crops"]

                    # 安全设置
                    if "run_mode" in body:
                        cfg.safety.run_mode = body["run_mode"]
                    if "random_delay_min" in body:
                        cfg.safety.random_delay_min = float(body["random_delay_min"])
                    if "random_delay_max" in body:
                        cfg.safety.random_delay_max = float(body["random_delay_max"])
                    if "click_offset" in body:
                        cfg.safety.click_offset_range = int(body["click_offset"])

                    # 静默时段
                    sh = cfg.silent_hours
                    if "silent_enabled" in body: sh.enabled = bool(body["silent_enabled"])
                    if "silent_start_hour" in body: sh.start_hour = int(body["silent_start_hour"])
                    if "silent_start_minute" in body: sh.start_minute = int(body["silent_start_minute"])
                    if "silent_end_hour" in body: sh.end_hour = int(body["silent_end_hour"])
                    if "silent_end_minute" in body: sh.end_minute = int(body["silent_end_minute"])

                    # 其他
                    if "window_keyword" in body:
                        cfg.window_title_keyword = body["window_keyword"]

                    cfg.save()
                    eng.update_config(cfg)
                    return JSONResponse({"ok": True})
                except Exception as e:
                    logger.exception("保存配置失败")
                    return JSONResponse({"ok": False, "msg": str(e)})

            @app.get("/api/logs")
            async def api_logs():
                """获取最近 50 条日志"""
                try:
                    import glob
                    import os
                    log_files = sorted(glob.glob("logs/*.log"), key=os.path.getmtime, reverse=True)
                    lines = []
                    if log_files:
                        with open(log_files[0], "r", encoding="utf-8") as f:
                            all_lines = f.readlines()
                            lines = [l.strip() for l in all_lines[-50:] if l.strip()]
                    return JSONResponse(lines)
                except Exception:
                    return JSONResponse([])

            @app.get("/api/screenshot")
            async def api_screenshot():
                img = self.get_screenshot()
                if img is None:
                    return Response(content=b"", media_type="image/jpeg", status_code=204)
                # 转 JPEG
                try:
                    if isinstance(img, np.ndarray):
                        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
                        data = buf.tobytes()
                    else:
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=75)
                        data = buf.getvalue()
                    return Response(content=data, media_type="image/jpeg")
                except Exception as e:
                    logger.warning(f"截图转换失败: {e}")
                    return Response(content=b"", media_type="image/jpeg", status_code=204)

            @app.post("/api/start")
            async def api_start():
                self.start_bot()
                return JSONResponse({"ok": True})

            @app.post("/api/stop")
            async def api_stop():
                self.stop_bot()
                return JSONResponse({"ok": True})

            @app.post("/api/pause")
            async def api_pause():
                self.pause_bot()
                return JSONResponse({"ok": True})

            @app.post("/api/resume")
            async def api_resume():
                self.resume_bot()
                return JSONResponse({"ok": True})

            config = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning")
            self._server = uvicorn.Server(config)
            
            # 在运行前检查是否已被停止
            if not self._running:
                logger.info("Web 服务在启动前已被停止")
                return
                
            try:
                self._server.run()
            except KeyboardInterrupt:
                logger.info("Web 服务收到中断信号")
            except SystemExit:
                logger.info("Web 服务收到退出信号")
                
            # run() 返回后，再次检查状态
            if not self._running:
                logger.info("Web 服务已退出运行循环")
        except Exception as e:
            logger.exception(f"Web 服务异常: {e}")
            self._running = False


# ── Web 页面 HTML ─────────────────────────────────────────────────────
_PAGE_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QQ Farm Bot - Web 控制</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
     background:#f0f2f5;color:#1d1d1f;min-height:100vh}
.header{background:#fff;padding:16px 24px;box-shadow:0 1px 3px rgba(0,0,0,.1);
        display:flex;align-items:center;gap:12px}
.header h1{font-size:20px;font-weight:600}
.status{display:inline-block;padding:4px 12px;border-radius:12px;font-size:12px;font-weight:500}
.status.stopped{background:#f5f5f5;color:#666}
.status.running{background:#e6f7e9;color:#1a7f37}
.status.paused{background:#fff8e6;color:#b45309}
.container{max-width:1200px;margin:24px auto;padding:0 16px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:768px){.grid{grid-template-columns:1fr}}
.card{background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.card h2{font-size:16px;font-weight:600;margin-bottom:12px}
.screenshot{width:100%;border-radius:8px;background:#000;min-height:200px;
            display:flex;align-items:center;justify-content:center;color:#666}
.screenshot img{max-width:100%;border-radius:8px}
.btn-group{display:flex;gap:8px;flex-wrap:wrap}
.btn{padding:10px 20px;border:none;border-radius:8px;font-size:14px;font-weight:500;
     cursor:pointer;transition:all .15s}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-primary{background:#007aff;color:#fff}
.btn-primary:hover:not(:disabled){background:#0066d6}
.btn-danger{background:#ff3b30;color:#fff}
.btn-danger:hover:not(:disabled){background:#d62f26}
.btn-warning{background:#ff9500;color:#fff}
.btn-warning:hover:not(:disabled){background:#e08600}
.btn-success{background:#34c759;color:#fff}
.btn-success:hover:not(:disabled){background:#2db84e}
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(100px,1fr));gap:10px}
.stat-item{background:#f8f9fa;border-radius:8px;padding:10px;text-align:center}
.stat-value{font-size:22px;font-weight:700;color:#007aff}
.stat-label{font-size:11px;color:#666;margin-top:4px}
.schedule{display:flex;flex-direction:column;gap:8px}
.schedule-row{display:flex;justify-content:space-between;padding:8px 12px;background:#f8f9fa;border-radius:8px}
.schedule-row .label{color:#666;font-size:13px}
.schedule-row .value{font-weight:600;font-size:13px;color:#007aff}
.config-section{margin-bottom:12px}
.config-section h3{font-size:13px;font-weight:600;color:#666;margin-bottom:8px}
.cfg-row{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.cfg-row label{font-size:12px;color:#333;min-width:80px}
.cfg-row input[type=number]{width:80px;padding:4px 8px;border:1px solid #ddd;border-radius:6px;font-size:12px}
.cfg-row input[type=time]{padding:4px 8px;border:1px solid #ddd;border-radius:6px;font-size:12px}
.cfg-row .sw{display:flex;align-items:center;gap:4px}
.cfg-row .sw input{width:16px;height:16px;cursor:pointer}
.save-btn{margin-top:8px;padding:8px 20px;background:#007aff;color:#fff;border:none;
           border-radius:8px;font-size:13px;font-weight:500;cursor:pointer}
.save-btn:hover{background:#0066d6}
.save-btn:disabled{opacity:.5;cursor:not-allowed}
.logs{max-height:200px;overflow-y:auto;background:#1e1e1e;color:#d4d4d4;
      font-family:'Cascadia Code',Consolas,monospace;font-size:11px;
      padding:10px;border-radius:8px;line-height:1.6}
.logs div{white-space:nowrap}
.logs .INFO{color:#6a9955}
.logs .WARNING{color:#dcdcaa}
.logs .ERROR{color:#f44747}
</style>
</head>
<body>
<div class="header">
  <h1>🌾 QQ Farm Bot</h1>
  <span id="status" class="status stopped">已停止</span>
</div>
<div class="container">
  <div class="grid">
    <div class="card">
      <h2>📷 实时截图</h2>
      <div class="screenshot" id="screenshot"><span>等待截图...</span></div>
    </div>
    <div class="card">
      <h2>🎮 控制面板</h2>
      <div class="btn-group">
        <button class="btn btn-success" id="btn-start" onclick="doAction('start')">▶ 启动</button>
        <button class="btn btn-warning" id="btn-pause" onclick="doAction('pause')" disabled>⏸ 暂停</button>
        <button class="btn btn-primary" id="btn-resume" onclick="doAction('resume')" disabled>⏯ 恢复</button>
        <button class="btn btn-danger" id="btn-stop" onclick="doAction('stop')" disabled>⏹ 停止</button>
      </div>
      <h2 style="margin-top:20px">📊 统计数据</h2>
      <div class="stats" id="stats"></div>
      <h2 style="margin-top:20px">⏰ 下次巡查</h2>
      <div class="schedule">
        <div class="schedule-row"><span class="label">农场巡查</span><span class="value" id="next-farm">--</span></div>
        <div class="schedule-row"><span class="label">好友巡查</span><span class="value" id="next-friend">--</span></div>
      </div>
    </div>
  </div>
  <div class="grid" style="margin-top:16px">
    <div class="card">
      <h2>⚙️ 配置设置</h2>
      <div id="config-form"></div>
    </div>
    <div class="card">
      <h2>📝 最近日志</h2>
      <div class="logs" id="logs"><div>等待日志...</div></div>
    </div>
  </div>
</div>
<script>
const $=s=>document.querySelector(s);
let state='stopped',lastImgUrl=null;

function updateButtons(){
  $('#btn-start').disabled=state!=='stopped';
  $('#btn-pause').disabled=state!=='running';
  $('#btn-resume').disabled=state!=='paused';
  $('#btn-stop').disabled=state==='stopped';
}

function fmtCountdown(ts){
  if(!ts||ts<=0)return'等待中...';
  const diff=ts-Date.now()/1000;
  if(diff<=0)return'等待中...';
  const m=Math.floor(diff/60),s=Math.floor(diff%60);
  return m>0?m+'分'+s+'秒':s+'秒';
}

function buildConfigForm(c){
  if(!c||!c.farm_check_seconds)return'<div style="color:#999">配置未加载</div>';
  const fmt=(v,d=0)=>v!==undefined?v:(d===0?'0':d);
  const time=(h,m)=>`${String(fmt(h,2)).padStart(2,'0')}:${String(fmt(m,0)).padStart(2,'0')}`;
  // 作物下拉框（异步填充）
  const cropOpts=`<option value="">加载中...</option>`;
  return`
  <div class="config-section"><h3>⏱️ 巡查间隔（秒）</h3>
    <div class="cfg-row"><label>农场</label><input type="number" id="cf-farm" value="${c.farm_check_seconds}" min="1"></div>
    <div class="cfg-row"><label>好友</label><input type="number" id="cf-friend" value="${c.friend_check_seconds}" min="1"></div>
  </div>
  <div class="config-section"><h3>🌱 种植设置</h3>
    <div class="cfg-row"><label>玩家等级</label><input type="number" id="cf-level" value="${c.player_level}" min="1"></div>
    <div class="cfg-row"><label>策略</label><select id="cf-strategy" style="padding:4px;border-radius:6px;border:1px solid #ddd">
      <option value="preferred" ${c.plant_strategy==='preferred'?'selected':''}>手动指定</option>
      <option value="best_exp_rate" ${c.plant_strategy==='best_exp_rate'?'selected':''}>自动最优</option></select></div>
    <div class="cfg-row"><label>指定作物</label><select id="cf-crop" style="padding:4px;border-radius:6px;border:1px solid #ddd">${cropOpts}</select></div>
  </div>
  <div class="config-section"><h3>⚙️ 功能开关</h3>
    <div class="cfg-row"><div class="sw"><input type="checkbox" id="cf-harvest" ${c.auto_harvest?'checked':''}><label>收获</label></div>
    <div class="sw"><input type="checkbox" id="cf-plant" ${c.auto_plant?'checked':''}><label>播种</label></div>
    <div class="sw"><input type="checkbox" id="cf-fertilize" ${c.auto_fertilize?'checked':''}><label>施肥</label></div>
    <div class="sw"><input type="checkbox" id="cf-buyseed" ${c.auto_buy_seed?'checked':''}><label>买种</label></div></div>
    <div class="cfg-row"><div class="sw"><input type="checkbox" id="cf-water" ${c.auto_water?'checked':''}><label>浇水</label></div>
    <div class="sw"><input type="checkbox" id="cf-weed" ${c.auto_weed?'checked':''}><label>除草</label></div>
    <div class="sw"><input type="checkbox" id="cf-bug" ${c.auto_bug?'checked':''}><label>除虫</label></div>
    <div class="sw"><input type="checkbox" id="cf-sell" ${c.auto_sell?'checked':''}><label>出售</label></div></div>
    <div class="cfg-row"><div class="sw"><input type="checkbox" id="cf-task" ${c.auto_task?'checked':''}><label>任务</label></div>
    <div class="sw"><input type="checkbox" id="cf-upgrade" ${c.auto_upgrade?'checked':''}><label>扩建</label></div></div>
  </div>
  <div class="config-section"><h3>👥 好友操作</h3>
    <div class="cfg-row"><div class="sw"><input type="checkbox" id="cf-steal" ${c.friend_steal?'checked':''}><label>偷菜</label></div>
    <div class="sw"><input type="checkbox" id="cf-fweed" ${c.friend_weed?'checked':''}><label>帮忙除草</label></div>
    <div class="sw"><input type="checkbox" id="cf-fwater" ${c.friend_water?'checked':''}><label>帮忙浇水</label></div>
    <div class="sw"><input type="checkbox" id="cf-fbug" ${c.friend_bug?'checked':''}><label>帮忙除虫</label></div></div>
    <div class="cfg-row"><label>偷菜上限/轮</label><input type="number" id="cf-maxsteal" value="${c.max_steal_per_round||0}" min="0"></div>
  </div>
  <div class="config-section"><h3>📊 出售策略</h3>
    <div class="cfg-row"><label>模式</label><select id="cf-sellmode" style="padding:4px;border-radius:6px;border:1px solid #ddd">
      <option value="batch_all" ${c.sell_mode==='batch_all'?'selected':''}>批量全部</option>
      <option value="selective" ${c.sell_mode==='selective'?'selected':''}>选择性</option></select></div>
  </div>
  <div class="config-section"><h3>🛡️ 安全设置</h3>
    <div class="cfg-row"><label>运行模式</label><select id="cf-runmode" style="padding:4px;border-radius:6px;border:1px solid #ddd">
      <option value="background" ${c.run_mode==='background'?'selected':''}>后台</option>
      <option value="foreground" ${c.run_mode==='foreground'?'selected':''}>前台</option></select></div>
    <div class="cfg-row"><label>延迟范围(秒)</label><input type="number" id="cf-dmin" value="${c.random_delay_min}" step="0.1" min="0" style="width:60px"> ~ <input type="number" id="cf-dmax" value="${c.random_delay_max}" step="0.1" min="0" style="width:60px"></div>
    <div class="cfg-row"><label>点击偏移(像素)</label><input type="number" id="cf-offset" value="${c.click_offset}" min="0" style="width:60px"></div>
  </div>
  <div class="config-section"><h3>🌙 静默时段</h3>
    <div class="cfg-row"><div class="sw"><input type="checkbox" id="cf-silent-on" ${c.silent_enabled?'checked':''}><label>启用</label></div></div>
    <div class="cfg-row"><label>开始</label><input type="time" id="cf-silent-start" value="${time(c.silent_start_hour,c.silent_start_minute)}"></div>
    <div class="cfg-row"><label>结束</label><input type="time" id="cf-silent-end" value="${time(c.silent_end_hour,c.silent_end_minute)}"></div>
  </div>
  <div class="config-section"><h3>🔍 其他</h3>
    <div class="cfg-row"><label>窗口关键词</label><input type="text" id="cf-winkey" value="${c.window_keyword||''}" style="padding:4px 8px;border:1px solid #ddd;border-radius:6px"></div>
  </div>
  <button class="save-btn" onclick="saveConfig()">💾 保存配置</button>`;
}

async function loadCrops(selectedCrop){
  try{
    const r=await fetch('/api/crops');
    const crops=await r.json();
    const sel=document.getElementById('cf-crop');
    if(!sel)return;
    sel.innerHTML=crops.map(c=>`<option value="${c.name}" ${c.name===selectedCrop?'selected':''}>${c.name} (Lv${c.level})</option>`).join('');
  }catch(e){console.error('加载作物失败',e)}
}

async function saveConfig(){
  const v=id=>document.getElementById(id);
  if(!v('cf-farm')){alert('配置表单未加载，请刷新页面');return;}
  const val=id=>v(id).value;
  const chk=id=>v(id).checked;
  const[startH,startM]=val('cf-silent-start').split(':').map(Number);
  const[endH,endM]=val('cf-silent-end').split(':').map(Number);
  const body={
    farm_check_seconds:+val('cf-farm'),friend_check_seconds:+val('cf-friend'),
    player_level:+val('cf-level'),plant_strategy:val('cf-strategy'),preferred_crop:val('cf-crop'),
    auto_harvest:chk('cf-harvest'),auto_plant:chk('cf-plant'),auto_fertilize:chk('cf-fertilize'),
    auto_buy_seed:chk('cf-buyseed'),auto_water:chk('cf-water'),auto_weed:chk('cf-weed'),
    auto_bug:chk('cf-bug'),auto_sell:chk('cf-sell'),auto_task:chk('cf-task'),auto_upgrade:chk('cf-upgrade'),
    friend_steal:chk('cf-steal'),friend_weed:chk('cf-fweed'),friend_water:chk('cf-fwater'),
    friend_bug:chk('cf-fbug'),max_steal_per_round:+val('cf-maxsteal'),
    sell_mode:val('cf-sellmode'),
    run_mode:val('cf-runmode'),random_delay_min:+val('cf-dmin'),random_delay_max:+val('cf-dmax'),
    click_offset:+val('cf-offset'),
    silent_enabled:chk('cf-silent-on'),silent_start_hour:startH,silent_start_minute:startM,
    silent_end_hour:endH,silent_end_minute:endM,
    window_keyword:val('cf-winkey'),
  };
  try{
    const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(!r.ok){alert('HTTP '+r.status+': '+(await r.text()));return;}
    const d=await r.json();
    if(d.ok)alert('配置已保存');else alert('保存失败: '+(d.msg||'未知错误'));
    refresh();
  }catch(e){alert('保存失败: '+e.message)}
}

async function refresh(){
  try{
    const r=await fetch('/api/status?t='+Date.now());
    const d=await r.json();
    state=d.state;
    $('#status').className='status '+state;
    $('#status').textContent={running:'运行中',paused:'已暂停',stopped:'已停止'}[state]||state;
    updateButtons();
    // 统计（已是中文）
    const stats=d.stats||{};
    let h='';
    for(const[k,v]of Object.entries(stats)){if(v===0)continue;h+=`<div class="stat-item"><div class="stat-value">${v}</div><div class="stat-label">${k}</div></div>`;}
    $('#stats').innerHTML=h||'<div style="color:#999;grid-column:1/-1">暂无数据</div>';
    // 下次巡查
    $('#next-farm').textContent=fmtCountdown(d.next_farm_check);
    $('#next-friend').textContent=fmtCountdown(d.next_friend_check);
    // 配置表单
    if(d.config&&$('#config-form').innerHTML.length<20){
      $('#config-form').innerHTML=buildConfigForm(d.config);
      loadCrops(d.config.preferred_crop||'');
    }
  }catch(e){console.error(e)}
  // 截图（加时间戳防止缓存）
  try{
    const r=await fetch('/api/screenshot?t='+Date.now());
    if(r.ok&&r.status!==204){
      const blob=await r.blob();
      const url=URL.createObjectURL(blob);
      if(lastImgUrl)URL.revokeObjectURL(lastImgUrl);
      lastImgUrl=url;
      $('#screenshot').innerHTML=`<img src="${url}" alt="截图">`;
    }
  }catch(e){}
  // 日志
  try{
    const r=await fetch('/api/logs');
    const logs=await r.json();
    if(logs.length){
      $('#logs').innerHTML=logs.map(l=>{
        const cls=l.includes('ERROR')?'ERROR':l.includes('WARNING')?'WARNING':'INFO';
        return`<div class="${cls}">${l}</div>`;
      }).join('');
      $('#logs').scrollTop=$('#logs').scrollHeight;
    }
  }catch(e){}
}

async function doAction(action){
  try{
    await fetch('/api/'+action,{method:'POST'});
    // 立即刷新状态和按钮
    setTimeout(refresh,200);
    setTimeout(refresh,800);
  }catch(e){console.error(e)}
}

setInterval(refresh,2000);
refresh();
</script>
</body>
</html>
"""
