"""School login challenges and one-time transfer of the verified session."""
from __future__ import annotations

import base64
import logging
import secrets
import threading
import time
from datetime import datetime
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

CAS = 'https://cas.guet.edu.cn/authserver'
# 与真实登录成功时 CAS 使用的 service 一致（抓包证实），登录后 CAS 出票会重定向到
# /student/sso/login?ticket=... 再由教务 302 到 /student/home。
SERVICE = 'https://bkjwtest.guet.edu.cn/student/sso/login'
HOME = 'https://bkjwtest.guet.edu.cn/student/home'
# 教务会话存在性接口（真实页面每次进主页都会调用，返回 accountAssoc / loginDateTime）。
LOGIN_INFO = 'https://bkjwtest.guet.edu.cn/student/home/get-account-login-info'
CHARS = 'ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678'
TTL = 300
# loginDateTime 只用于诊断日志：它是教务维护的「账号最近一次登录时间」，SSO 票据登录
# 常常不刷新。超过该阈值仅记录一条 info，不阻断登录。
STALE_SECONDS = 600
_lock = threading.Lock()
_challenges = {}
_transfers = {}
logger = logging.getLogger('uvicorn.error')


class SchoolAuthError(Exception):
    pass


def _allowed(url):
    parsed = urlparse(str(url))
    return parsed.scheme == 'https' and not parsed.username and not parsed.password and (parsed.hostname == 'guet.edu.cn' or
                                       (parsed.hostname or '').endswith('.guet.edu.cn'))


def _request(client, method, url, **kwargs):
    # Keep every redirect inside the school's HTTPS domain.
    for _ in range(12):
        if not _allowed(url):
            raise SchoolAuthError('学校登录跳转到未允许的地址')
        response = client.request(method, url, follow_redirects=False, **kwargs)
        if not response.is_redirect:
            return response
        target = response.headers.get('location')
        if not target:
            raise SchoolAuthError('学校登录跳转无效')
        url = str(response.url.join(target))
        parsed = urlparse(url)
        if (parsed.scheme == 'http' and not parsed.username and not parsed.password and parsed.port in (None, 80)
                and (parsed.hostname == 'guet.edu.cn' or (parsed.hostname or '').endswith('.guet.edu.cn'))):
            url = parsed._replace(scheme='https', netloc=parsed.hostname).geturl()
        if response.status_code in (301, 302, 303):
            method, kwargs = 'GET', {}
    raise SchoolAuthError('学校登录跳转次数过多')


def _purge():
    now = time.monotonic()
    for key, value in list(_challenges.items()):
        if value['expires'] <= now:
            _challenges.pop(key)['client'].close()
    for key, value in list(_transfers.items()):
        if value['expires'] <= now:
            _transfers.pop(key, None)


def _age_seconds(stamp):
    """loginDateTime 距当前的最小间隔（秒）；无法解析返回 None。

    学校接口给的是不带时区的本地时间，无法确定是北京时间还是 UTC，所以按「本机时间」
    与「UTC 时间」各算一次，取绝对值较小者，避免时区解释错误把正常登录判成旧会话。
    """
    try:
        moment = datetime.strptime(str(stamp).strip(), '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return None
    return min(abs((reference - moment).total_seconds())
               for reference in (datetime.now(), datetime.utcnow()))


def _login_info(client):
    """确认教务会话真实有效：返回账号登录信息，失败返回 None。

    以 /student/home/get-account-login-info 为准——它是主页自身就会调用的接口，返回
    accountAssoc（内部账号 id）与本次登录时间。不再解析 /student/my/account 的 DOM：
    该路径在真实流量中从未出现，且单页应用的输入框不会有服务端回填的学号。
    """
    try:
        response = _request(client, 'GET', LOGIN_INFO)
    except SchoolAuthError:
        raise
    if response.status_code != 200 or not response.headers.get('content-type', '').lower().startswith('application/json'):
        logger.warning('school_login_info_unavailable status=%s', response.status_code)
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get('accountAssoc'), int):
        return None
    return data


def _encrypt(password, salt):
    if len(salt.encode('utf-8')) != 16:
        raise SchoolAuthError('学校登录页面发生变化')
    random_text = lambda n: ''.join(secrets.choice(CHARS) for _ in range(n))
    data = (random_text(64) + password).encode('utf-8')
    padder = padding.PKCS7(128).padder()
    body = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(salt.encode('utf-8')), modes.CBC(random_text(16).encode('ascii'))).encryptor()
    return base64.b64encode(encryptor.update(body) + encryptor.finalize()).decode('ascii')


def begin(username):
    username = username.strip()
    if not username or len(username) > 64:
        raise SchoolAuthError('请输入学号或工号')
    client = httpx.Client(timeout=20.0, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        page = _request(client, 'GET', CAS + '/login', params={'service': SERVICE})
        if page.status_code != 200 or urlparse(str(page.url)).hostname != 'cas.guet.edu.cn':
            raise SchoolAuthError('学校登录页面暂时不可用')
        soup = BeautifulSoup(page.text, 'html.parser')
        salt_node = soup.select_one('#pwdEncryptSalt')
        marker = soup.select_one('[value="userNameLogin"]')
        form = marker.find_parent('form') if marker else None
        execution = form.select_one('[name="execution"]') if form else None
        salt = salt_node.get('value', '') if salt_node else ''
        if not salt or not execution or not execution.get('value'):
            raise SchoolAuthError('学校登录表单结构发生变化')
        check = _request(client, 'GET', CAS + '/checkNeedCaptcha.htl', params={'username': username})
        try:
            needed = check.json()['isNeed']
        except Exception as exc:
            raise SchoolAuthError('无法确认学校验证码要求') from exc
        if not isinstance(needed, bool):
            raise SchoolAuthError('无法确认学校验证码要求')
        image = None
        if needed:
            response = _request(client, 'GET', CAS + '/getCaptcha.htl', params={'_': str(int(time.time() * 1000))})
            if response.status_code != 200 or not response.headers.get('content-type', '').lower().startswith('image/'):
                raise SchoolAuthError('学校验证码获取失败')
            image = 'data:' + response.headers['content-type'].split(';')[0] + ';base64,' + base64.b64encode(response.content).decode('ascii')
        challenge_id = secrets.token_urlsafe(32)
        with _lock:
            _purge()
            _challenges[challenge_id] = {'client': client, 'username': username, 'salt': salt,
                                         'execution': execution['value'], 'needed': needed,
                                         'expires': time.monotonic() + TTL}
        return {'challenge_id': challenge_id, 'captcha_required': needed, 'captcha_image': image}
    except Exception:
        client.close()
        raise


def finish(challenge_id, username, password, captcha):
    with _lock:
        _purge()
        item = _challenges.pop(challenge_id, None)
    if not item:
        raise SchoolAuthError('登录请求已失效，请重新获取验证码')
    client = item['client']
    try:
        if username.strip() != item['username'] or not password:
            raise SchoolAuthError('登录信息已变更，请重新开始')
        if item['needed'] and not captcha.strip():
            raise SchoolAuthError('请输入学校验证码')
        form = {'username': item['username'], 'password': _encrypt(password, item['salt']),
                'captcha': captcha.strip(), 'lt': '', 'execution': item['execution'],
                '_eventId': 'submit', 'cllt': 'userNameLogin', 'dllt': 'generalLogin', 'rememberMe': 'true'}
        login = _request(client, 'POST', CAS + '/login', params={'service': SERVICE}, data=form,
                         headers={'Referer': CAS + '/login', 'Origin': 'https://cas.guet.edu.cn'})
        landed = urlparse(str(login.url))
        if login.status_code != 200 or landed.hostname != 'bkjwtest.guet.edu.cn' or landed.path.rstrip('/') != '/student/home':
            html = BeautifulSoup(login.text, 'html.parser')
            error = html.select_one('#showErrorTip')
            tip = error.get_text(' ', strip=True) if error else ''
            category = 'captcha' if '验证码' in tip else ('credentials' if any(x in tip for x in ('密码', '用户名', '账号')) else ('other' if tip else 'none'))
            logger.warning('school_login_rejected host=%s status=%s category=%s landed=%s landed_path=%s',
                           landed.hostname, login.status_code, category, landed.hostname, landed.path)
            # 停在 CAS 域说明凭据/验证码/service 授权被拒；落到别处说明跳转链没走完。
            if category == 'captcha':
                raise SchoolAuthError('学校验证码不正确，请重新获取后重试')
            if category == 'credentials':
                raise SchoolAuthError('账号或密码不正确，请核对后重试')
            if landed.hostname == 'cas.guet.edu.cn':
                raise SchoolAuthError('账号或密码不正确，或该账号无权访问教务')
            raise SchoolAuthError('学校登录未完成，请稍后重试')
        # 主判据已成立（CAS 为本次输入的账号出票，并落到教务主页）。再用教务自身的
        # 会话接口确认会话真实可用，避免拿到票据但会话未建立。
        info = _login_info(client)
        if not info:
            raise SchoolAuthError('教务会话未建立，请稍后重试')
        # loginDateTime 只做诊断，不参与判定：它是教务维护的「账号最近一次登录时间」，
        # 经 SSO 票据登录时经常不刷新（实测出现过 32 分钟前、甚至两天前的值），拿它
        # 做新鲜度硬判据会把成功的登录误杀。
        age = _age_seconds(info.get('loginDateTime'))
        if age is not None and age > STALE_SECONDS:
            logger.info('school_login_time_not_refreshed loginDateTime=%s age=%ss',
                        info.get('loginDateTime'), int(age))
        cookies = []
        for cookie in client.cookies.jar:
            domain = cookie.domain.lstrip('.')
            if domain != 'guet.edu.cn' and not domain.endswith('.guet.edu.cn'):
                continue
            cookies.append({'name': cookie.name, 'value': cookie.value, 'domain': cookie.domain,
                            'path': cookie.path or '/', 'secure': bool(cookie.secure),
                            'httpOnly': cookie.has_nonstandard_attr('HttpOnly'),
                            'domainSpecified': cookie.domain_specified, 'expires': cookie.expires})
        if not cookies:
            raise SchoolAuthError('学校登录会话不可用')
        return item['username'], cookies
    finally:
        client.close()


def offer_transfer(token, user_id, cookies):
    with _lock:
        _purge()
        _transfers[token] = {'user_id': user_id, 'cookies': cookies, 'expires': time.monotonic() + TTL}


def take_transfer(token, user_id):
    with _lock:
        _purge()
        value = _transfers.get(token)
    if not value or value['user_id'] != user_id:
        raise SchoolAuthError('学校登录会话已失效，请重新登录')
    # 不立即 pop：桌面端导入 Cookie 可能因窗口未就绪等原因失败，保留到 TTL 到期，
    # 允许同一登录状态内重试一次，避免「认证已通过却要重新登录」。
    return value['cookies']


# ── 服务端会话：给网页端查询用 ────────────────────────────────────────────────
#
# 与上面 offer_transfer 的区别：
#   offer_transfer 是「一次性移交给桌面端」，桌面端取走后由它自己带着会话发请求；
#   这里的 _sessions 是「服务端自己留一份」，因为网页端没有 Electron、没有内嵌学校
#   页面，只能由后端代发 free-list 请求。
#
# ★ 只放内存、不落盘：进程重启即失效。会话凭据等价于该学生的教务系统访问权，
#   不该写进数据库或日志。代价是重启后要重新登录一次，这个代价是值当的。
# ★ 有效期 2 小时：教务 Cookie 的实际寿命不受我们控制，但服务端代持的窗口越短越好；
#   过期后查询会返回 401 并提示重新认证，前端只需再登录一次学校账号。
_sessions = {}
SESSION_TTL = 2 * 3600


def _purge_sessions():
    now = time.monotonic()
    for key in [k for k, value in _sessions.items() if value['expires'] <= now]:
        _sessions.pop(key, None)


def remember_session(user_id, cookies):
    """登录成功后把教务会话留在服务端（按用户隔离）。"""
    if not cookies:
        return
    with _lock:
        _purge_sessions()
        _sessions[int(user_id)] = {'cookies': cookies, 'expires': time.monotonic() + SESSION_TTL}


def session_cookies(user_id):
    """取该用户的服务端会话；没有或已过期返回 None。"""
    with _lock:
        _purge_sessions()
        value = _sessions.get(int(user_id))
    return list(value['cookies']) if value else None


def clear_session(user_id):
    with _lock:
        _sessions.pop(int(user_id), None)


def has_session(user_id):
    return session_cookies(user_id) is not None
