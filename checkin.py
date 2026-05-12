#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GLaDOS / Railgun 自动签到 + 自动兑换

功能：
- 全自动签到 (glados.cloud + railgun.info 双域名)
- 积分查询 & 自动兑换
- PushPlus 微信推送 / Telegram 推送
- 多账号支持
"""

import requests
import json
import os
import sys
import re
import logging.config
import datetime
from typing import Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, asdict
from enum import Enum

# ================= 日志 =================


def beijing_time_converter(timestamp):
    utc_dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
    beijing_tz = datetime.timezone(datetime.timedelta(hours=8))
    return utc_dt.astimezone(beijing_tz).timetuple()


LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s - %(levelname)s - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
            "converter": beijing_time_converter,
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "level": "INFO",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger()


# ================= 枚举 & 常量 =================


class CheckinStatus(Enum):
    SUCCESS = 0
    REPEAT = 1
    FAILURE = -2


class LogEmoji:
    SUCCESS = "✅"
    FAIL = "❌"
    REPEAT = "🔄"
    CHECKIN = "🎫"
    STATUS = "📊"
    POINTS = "💰"
    EXCHANGE = "🎁"
    START = "🚀"
    END = "🏁"
    COOKIE = "🍪"
    DOMAIN = "🌐"
    WARNING = "⚠️"
    ERROR = "🔴"
    INFO = "ℹ️"


DOMAINS = ["railgun.info"]

EXCHANGE_PLANS = {
    "plan100": 100,
    "plan200": 200,
    "plan500": 500,
}


# ================= Cookie 解析 =================


def extract_cookie(raw: str) -> Optional[str]:
    """提取 Cookie，支持多种格式"""
    if not raw:
        return None
    raw = raw.strip()

    # Cookie-Editor 格式 (koa:sess=xxx; koa:sess.sig=yyy)
    if "koa:sess=" in raw or "koa:sess.sig=" in raw:
        return raw

    # JSON
    if raw.startswith("{"):
        try:
            return "koa:sess=" + json.loads(raw).get("token", "")
        except Exception:
            pass

    # JWT Token
    if raw.count(".") == 2 and "=" not in raw and len(raw) > 50:
        return "koa:sess=" + raw

    # Standard cookie string
    return raw


def get_cookies() -> List[str]:
    """从环境变量读取并解析 cookies"""
    raw = os.environ.get("GLADOS_COOKIES", "")
    if not raw:
        logger.error(f"{LogEmoji.ERROR} 未配置 GLADOS_COOKIES 环境变量")
        return []

    sep = "\n" if "\n" in raw else "&"
    cookies = []
    for c in raw.split(sep):
        parsed = extract_cookie(c)
        if parsed:
            cookies.append(parsed)
    return cookies


# ================= API 客户端 =================


class API:
    """HTTP API 客户端，使用 Session 保持连接"""

    def __init__(self, domain: str, cookie_index: int = 0, verbose: bool = False):
        self.domain = domain
        self.cookie_index = cookie_index
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({
            "origin": f"https://{self.domain}",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def close(self):
        if hasattr(self, "session"):
            try:
                self.session.close()
            except Exception:
                pass

    def _log(self, level: str, emoji: str, message: str, force: bool = False):
        msg = f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {emoji} {message}"
        if force or self.verbose:
            getattr(logger, level)(msg)

    def _make_request(self, path: str, method: str, data: Optional[Dict] = None,
                      cookies: str = "") -> Optional[requests.Response]:
        url = f"https://{self.domain}{path}"
        headers = {"cookie": cookies}
        try:
            if method.upper() == "POST":
                resp = self.session.post(url, headers=headers, data=json.dumps(data), timeout=(60, 120))
            else:
                resp = self.session.get(url, headers=headers, timeout=(60, 120))

            if not resp.ok:
                self._log("warning", LogEmoji.WARNING,
                          f"请求 {url} 失败，状态码 {resp.status_code}: {resp.text}", force=True)
                return None
            return resp
        except requests.exceptions.RequestException as e:
            self._log("error", LogEmoji.ERROR, f"请求 {url} 网络错误: {e}", force=True)
            return None

    def checkin(self, cookies: str) -> Dict:
        """执行签到"""
        response = self._make_request("/api/user/checkin", "POST",
                                      {"token": self.domain}, cookies)
        if response:
            data = response.json()
            code = data.get("code", -2)
            message = data.get("message", "")
            points = str(data.get("points", "0"))

            if code == CheckinStatus.SUCCESS.value:
                self._log("info", LogEmoji.SUCCESS,
                          f"签到成功: code={code}, points={points}, message={message}")
                return {"status": "签到成功", "points": points, "message": message, "code": CheckinStatus.SUCCESS}
            elif code == CheckinStatus.REPEAT.value:
                self._log("info", LogEmoji.REPEAT, f"重复签到: code={code}, message={message}", force=True)
                return {"status": "重复签到", "points": "0", "message": message, "code": CheckinStatus.REPEAT}
            else:
                self._log("info", LogEmoji.FAIL, f"签到失败: code={code}, message={message}", force=True)
                return {"status": "签到失败", "points": "0", "message": message, "code": CheckinStatus.FAILURE}

        self._log("warning", LogEmoji.WARNING, "签到请求失败", force=True)
        return {"status": "签到失败", "points": "0", "message": "网络请求失败", "code": CheckinStatus.FAILURE}

    def get_status(self, cookies: str) -> Tuple[str, int]:
        """获取剩余天数"""
        response = self._make_request("/api/user/status", "GET", cookies=cookies)
        if response:
            data = response.json()
            code = data.get("code", -2)
            left_days = data.get("data", {}).get("leftDays")
            if left_days is not None:
                days_int = int(float(left_days))
                self._log("info", LogEmoji.SUCCESS, f"剩余天数: {days_int}")
                return f"{days_int} 天", code
            return "None 天", code
        return "None 天", -2

    def get_points(self, cookies: str) -> Tuple[str, int]:
        """获取积分"""
        response = self._make_request("/api/user/points", "GET", cookies=cookies)
        if response:
            data = response.json()
            code = data.get("code", -2)
            points = data.get("points")
            if points is not None:
                pts = int(float(points))
                self._log("info", LogEmoji.SUCCESS, f"总积分: {pts}")
                return f"{pts} 积分", pts
            return "None 积分", 0
        return "None 积分", 0

    def exchange(self, cookies: str, plan: str, required_points: int) -> str:
        """执行兑换"""
        response = self._make_request("/api/user/exchange", "POST",
                                      {"planType": plan}, cookies)
        if response:
            data = response.json()
            code = data.get("code", -2)
            message = data.get("message", "未知错误")
            if code == 0:
                self._log("info", LogEmoji.SUCCESS, f"兑换成功: {plan} - {message}")
                return f"兑换成功: {plan}"
            else:
                self._log("info", LogEmoji.FAIL, f"兑换失败: {message}", force=True)
                return f"兑换失败: {message}"
        self._log("warning", LogEmoji.WARNING, "兑换请求失败", force=True)
        return "兑换失败"


# ================= 签到结果 =================


@dataclass
class CheckinResult:
    cookie_index: int
    domain: str
    status: str = "签到失败"
    points: str = "0"
    days: str = "None"
    points_total: str = "None"
    exchange: str = "未兑换"
    code: CheckinStatus = CheckinStatus.FAILURE

    def to_dict(self) -> Dict:
        return asdict(self)


# ================= 签到器 =================


class Checker:
    """签到编排器"""

    def __init__(self, cookies_list: List[str], exchange_plan: str, verbose: bool = False):
        self.cookies_list = cookies_list
        self.exchange_plan = exchange_plan
        self.verbose = verbose
        self.results: List[CheckinResult] = []

    def checkin_all(self):
        """对每个 Cookie x 每个域名执行签到"""
        cookie_count = len(self.cookies_list)
        domain_count = len(DOMAINS)
        total = cookie_count * domain_count
        task_idx = 0

        logger.info(f"{LogEmoji.INFO} 共 {cookie_count} 个 Cookie, {domain_count} 个域名, 共 {total} 个任务")

        for cookie_idx, cookie in enumerate(self.cookies_list, 1):
            logger.info(f"{LogEmoji.START} ========== Cookie {cookie_idx} ==========")

            for domain in DOMAINS:
                task_idx += 1
                logger.info(f"{LogEmoji.INFO} ----- 任务 {task_idx}/{total}: "
                            f"{LogEmoji.COOKIE}[{cookie_idx}] {LogEmoji.DOMAIN}[{domain}] -----")

                result = self._checkin_on_domain(cookie, cookie_idx, domain)
                self.results.append(result)

                if result.code == CheckinStatus.SUCCESS:
                    logger.info(f"{LogEmoji.COOKIE}[{cookie_idx}] {LogEmoji.DOMAIN}[{domain}] "
                                f"{LogEmoji.SUCCESS} {result.status}, 积分 {result.points}")
                else:
                    logger.info(f"{LogEmoji.COOKIE}[{cookie_idx}] {LogEmoji.DOMAIN}[{domain}] "
                                f"{LogEmoji.WARNING} {result.status}")

    def _checkin_on_domain(self, cookie: str, cookie_idx: int, domain: str) -> CheckinResult:
        result = CheckinResult(cookie_idx, domain)
        required_points = EXCHANGE_PLANS.get(self.exchange_plan, 500)

        with API(domain, cookie_idx, verbose=self.verbose) as api:
            # 1. 获取状态
            days_str, _ = api.get_status(cookie)
            result.days = days_str

            # 2. 签到
            checkin_res = api.checkin(cookie)
            result.status = checkin_res["status"]
            result.code = checkin_res.get("code", CheckinStatus.FAILURE)
            result.points = checkin_res.get("points", "0")

            # 3. 获取积分
            points_str, points_num = api.get_points(cookie)
            result.points_total = points_str

            # 4. 积分足够则自动兑换
            if points_num >= required_points:
                logger.info(f"{LogEmoji.EXCHANGE} 积分 {points_num} >= {required_points}, 尝试兑换 {self.exchange_plan}")
                result.exchange = api.exchange(cookie, self.exchange_plan, required_points)
            else:
                result.exchange = f"积分不足 ({points_num}/{required_points})"

        return result

    def format_results(self) -> Tuple[str, str, str]:
        """格式化结果用于推送和日志"""
        results = [r.to_dict() for r in self.results]

        success = sum(1 for r in results if r["code"] == CheckinStatus.SUCCESS)
        repeat = sum(1 for r in results if r["code"] == CheckinStatus.REPEAT)
        fail = sum(1 for r in results if r["code"] == CheckinStatus.FAILURE)

        title = f"GLaDOS 签到: 成功{success}, 失败{fail}, 重复{repeat}"

        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"#{i} P:{r['points']} 剩余:{r['days']} 总积分:{r['points_total']} "
                         f"| {r['status']} | {r['exchange']}")

        content = "\n".join(lines)
        return title, content, content


# ================= 推送 =================


def pushplus(token: str, title: str, content: str):
    """PushPlus 微信推送"""
    if not token:
        return
    try:
        url = "http://www.pushplus.plus/send"
        requests.get(url, params={
            "token": token, "title": title, "content": content, "template": "html"
        }, timeout=10)
        logger.info(f"{LogEmoji.SUCCESS} PushPlus 推送成功")
    except Exception as e:
        logger.error(f"{LogEmoji.FAIL} PushPlus 推送失败: {e}")


def telegram_push(token: str, chat_id: str, title: str, content: str):
    """Telegram 推送"""
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        text = f"<b>{title}</b>\n\n{content}"

        # 清理 HTML 为 Telegram 兼容格式
        text = text.replace("<br>", "\n")
        text = re.sub(r"<h3[^>]*>", "<b>", text)
        text = text.replace("</h3>", "</b>\n")
        text = re.sub(r"<(div|p)[^>]*>", "", text)
        text = re.sub(r"</(div|p)>", "\n", text)
        text = re.sub(r"<(span|small)[^>]*>", "", text)
        text = re.sub(r"</(span|small)>", "", text)
        text = re.sub(r"<(?!\/?(b|i|u|s|a|code|pre)\b)[^>]+>", "", text)
        lines = [line.strip() for line in text.split("\n")]
        text = "\n".join(lines)
        text = re.sub(r"\n\s*\n", "\n\n", text).strip()

        resp = requests.post(url, json={
            "chat_id": chat_id, "text": text, "parse_mode": "HTML"
        }, timeout=10)
        if resp.status_code == 200:
            logger.info(f"{LogEmoji.SUCCESS} Telegram 推送成功")
        else:
            logger.error(f"{LogEmoji.FAIL} Telegram 推送失败: {resp.text}")
    except Exception as e:
        logger.error(f"{LogEmoji.FAIL} Telegram 推送失败: {e}")


# ================= 主程序 =================


def main():
    logger.info(f"{LogEmoji.START} GLaDOS Checkin Starting...")

    # 1. 读取 cookies
    cookies = get_cookies()
    if not cookies:
        logger.error(f"{LogEmoji.ERROR} 未找到有效 Cookie, 退出")
        sys.exit(1)

    # 2. 兑换计划
    exchange_plan = os.environ.get("GLADOS_EXCHANGE_PLAN", "plan500")
    if exchange_plan not in EXCHANGE_PLANS:
        logger.warning(f"{LogEmoji.WARNING} 兑换计划 '{exchange_plan}' 无效, 使用默认 plan500")
        exchange_plan = "plan500"
    logger.info(f"{LogEmoji.INFO} 兑换计划: {exchange_plan} (需要 {EXCHANGE_PLANS[exchange_plan]} 积分)")

    # 3. 是否详细输出
    verbose = os.environ.get("GLADOS_VERBOSE", "").lower() in ("true", "1", "yes")

    # 4. 执行签到
    checker = Checker(cookies, exchange_plan, verbose=verbose)
    checker.checkin_all()

    # 5. 格式化结果
    title, content, log_content = checker.format_results()
    logger.info(f"\n{LogEmoji.END}========== 签到总结 ==========\n{title}\n{log_content}")

    # 6. 推送通知
    push_level = os.environ.get("PUSH_LEVEL", "all").lower()
    results_dict = [r.to_dict() for r in checker.results]
    success_cnt = sum(1 for r in results_dict if r["code"] == CheckinStatus.SUCCESS)

    if push_level == "fail_only" and success_cnt == len(cookies):
        logger.info(f"{LogEmoji.INFO} PUSH_LEVEL=fail_only, 全部成功, 跳过推送")
        return

    ptoken = os.environ.get("PUSHPLUS_TOKEN")
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if ptoken or (tg_token and tg_chat_id):
        if ptoken:
            pushplus(ptoken, title, content)
        if tg_token and tg_chat_id:
            telegram_push(tg_token, tg_chat_id, title, content)

    logger.info(f"{LogEmoji.END} 签到完成")


if __name__ == "__main__":
    main()
