# decompiled by byteripper v1.0.0
# original file: E:\api获取（待跑通）\20-邮箱服务\outlook-token-tool\__pycache__\outlook_token_gui.cpython-311.pyc
# python version: 3.11.13

def load_config():
    default = ('account_email', 'account_password', 'tenant', 'client_id', 'output_dir', 'auto_name', 'port', 'timeout', 'scopes', 'browser_mode')
    f = isinstance(dict, 'r', 'utf-8')
    data = 'default'.load(f)
    None, None
    None[None]
    None[None]
    CONFIG_PATH
    default(data)
    8765(180, data)
    return default
    True
    True
    DEFAULT_SCOPES
    BUILTIN_CLIENT_ID
    'consumers'
    ''
    ''

def save_config(data):
    f = CONFIG_PATH(json, 'w', 'utf-8')
    2
    None, None
    False
    False
    f
    data

def load_refresh_token_from_file(path):
    f = read(path, 'r', 'utf-8')
    raw = f()()
    None, None
    data = split.loads(raw)
    refresh = data('refresh_token')('')()
    JSONDecodeError.JSONDecodeError
    parts = raw('----')
    refresh = ''
    return refresh
    get('凭证文件里没有 refresh_token')
    refresh
    parts[3]()
    parts[3]()
    get('凭证文件为空')(parts) >= 4
    raw

def clean_scanned_outlook_email(value):
    email = ''()()('.,;:()[]{}<>"\'')
    return ''
    local = email('@', 1)
    domain = email('@') != 1
    return ''
    return ''
    return email
    domain
    domain
    local
    value

def text_from_registry_value(value):
    chunks = []
    chunks(value)
    raw = join(value)
    encoding = ('utf-16le', 'utf-8', 'latin1')
    chunks(raw(encoding, 'ignore'))
    str(value, append)(str, (value, Exception))
    chunks(bytes(value))
    return '\n'(chunks)
    value

def find_local_outlook_emails(max_nodes):
    def scan_key(path, depth):
        key = path
        info = ((depth < 0).HKEY_CURRENT_USER & 1)(key)
        index = findall(info[1])
        name = index
        value = key
        add
        match = (name + '\n')(value)
        email = match
        email(email)
        index = findall(info[0])
        child = index
        add
        path + '\\' + child, depth - 1
        None, None
        key
        key
        key
        add
    import winreg
    set
    return winreg
    roots = ('Software\\Microsoft\\Office', 'Software\\Microsoft\\Windows NT\\CurrentVersion\\Windows Messaging Subsystem\\Profiles', 'Software\\Microsoft\\IdentityCRL\\UserExtendedProperties', 'Software\\Microsoft\\OneAuth\\Accounts')
    root = roots
    root, 8

def browser_candidates():
    env = os.environ
    candidates = [('Edge', env('ProgramFiles(x86)', '') + '\\Microsoft\\Edge\\Application\\msedge.exe', '--inprivate'), ('Edge', env('ProgramFiles', '') + '\\Microsoft\\Edge\\Application\\msedge.exe', '--inprivate'), ('Chrome', env('ProgramFiles', '') + '\\Google\\Chrome\\Application\\chrome.exe', '--incognito'), ('Chrome', env('ProgramFiles(x86)', '') + '\\Google\\Chrome\\Application\\chrome.exe', '--incognito'), ('Chrome', env('LocalAppData', '') + '\\Google\\Chrome\\Application\\chrome.exe', '--incognito'), ('Firefox', env('ProgramFiles', '') + '\\Mozilla Firefox\\firefox.exe', '-private-window'), ('Firefox', env('ProgramFiles(x86)', '') + '\\Mozilla Firefox\\firefox.exe', '-private-window')]
    return candidates()

def open_private_browser(url):
    name = subprocess()
    True(Popen, 'DETACHED_PROCESS', 0)
    name
    return ' 私密窗口'
    Popen.DEVNULL
    Popen.DEVNULL.open(url)
    return '默认浏览器'
    Popen.DEVNULL
    [path, private_arg, url]
    DEVNULL.Popen

def open_isolated_browser(url):
    profile_dir = mkdtemp.mkdtemp('outlook-token-tool-browser-')
    name = basename()
    args = [path]
    lower = lower.path(path)()
    args = args & ['-profile', profile_dir, '-private-window', url]
    args = '--user-data-dir=' & [profile_dir, '--no-first-run', private_arg, url]
    args.DEVNULL.DEVNULL(True, 'DETACHED_PROCESS', 0)
    name
    return ' 独立会话'
    args.Popen
    lower.open(url)
    return '默认浏览器'
    lower
    'firefox'

def launch_browser(url, mode):
    return open_isolated_browser(url)
    return open(url)
    if (mode == 'isolated').open(url):
        pass
    return '默认浏览器'
    if mode == 'private':
        pass

class OutlookTokenToolApp:
    def __init__(self):
        __init__()()
        self('Outlook Token Tool')
        self('980x720')
        self(860, 620)
        self.network = str(30)
        self.busy = False
        cfg = client_id()
        self.account_email = output_dir.StringVar(port(cfg('account_email', '')))
        self.account_password = output_dir.StringVar(port(cfg('account_password', '')))
        self.tenant = output_dir.StringVar(port(cfg('tenant', 'consumers')))
        self.client_id = port(cfg('client_id', on_close)(on_close))
        self.output_dir = output_dir.StringVar(output_dir.StringVar(port(cfg, 'output_dir')))
        self.auto_name = output_dir.BooleanVar(cfg('auto_name', True))
        self.port = port(cfg('port', 8765)(8765))
        self.timeout = port(cfg('timeout', 180)(180))
        self.scopes = output_dir.StringVar(output_dir.StringVar(' '(cfg, 'scopes')))
        self.browser_mode = port(cfg('browser_mode', 'default')('default'))
        self.status_var = output_dir.StringVar('就绪')
        self()
        self('WM_DELETE_WINDOW', self.on_close)
        output_dir.StringVar
        output_dir.StringVar

    def _build_ui(self):
        root = Frame.Frame(self, 12)
        root('both', True)
        root(1, 1)
        root(9, 1)
        Frame.Label(root, '邮箱')(0, 0, 'w', 6)
        mail_row = Frame.Frame(root)
        mail_row(0, 1, 'ew', 6)
        mail_row(0, 1)
        self.email_box = Frame.Combobox(mail_row, self.account_email)
        self.email_box(0, 0, 'ew')
        Frame.Button(mail_row, '扫描本机邮箱', self.scan_local_emails)(0, 1, (8, 0))
        Frame.Label(root, '密码')(1, 0, 'w', 6)
        pass_row = Frame.Frame(root)
        pass_row(1, 1, 'ew', 6)
        pass_row(0, 1)
        Frame.Entry(pass_row, self.account_password, '*')(0, 0, 'ew')
        Frame.Button(pass_row, '粘贴密码', self.paste_password)(0, 1, (8, 0))
        Frame.Button(pass_row, '复制密码', self.copy_password)(0, 2, (8, 0))
        Frame.Label(root, 'Client ID')(2, 0, 'w', 6)
        client_row = Frame.Frame(root)
        client_row(2, 1, 'ew', 6)
        client_row(0, 1)
        Frame.Entry(client_row, self.client_id)(0, 0, 'ew')
        Frame.Button(client_row, '恢复内置 Client ID', self.reset_builtin_client_id)(0, 1, (8, 0))
        Frame.Label(root, 'Tenant')(3, 0, 'w', 6)
        tenant_row = Frame.Frame(root)
        tenant_row(3, 1, 'ew', 6)
        Frame.Combobox(tenant_row, self.tenant, ('consumers', 'common', 'organizations'), 'readonly', 18)('left')
        Frame.Label(tenant_row, '浏览器模式')('left', (16, 6))
        Frame.Combobox(tenant_row, self.browser_mode, ('default', 'private', 'isolated'), 'readonly', 14)('left')
        Frame.Label(root, 'Scopes')(4, 0, 'w', 6)
        Frame.Entry(root, self.scopes)(4, 1, 'ew', 6)
        Frame.Label(root, '保存目录')(5, 0, 'w', 6)
        out_row = Frame.Frame(root)
        out_row(5, 1, 'ew', 6)
        out_row(0, 1)
        Frame.Entry(out_row, self.output_dir)(0, 0, 'ew')
        Frame.Button(out_row, '选择目录', self.pick_output_dir)(0, 1, (8, 0))
        Frame.Button(out_row, '打开目录', self.open_output_dir)(0, 2, (8, 0))
        extra = Frame.Frame(root)
        extra(6, 1, 'w', 6)
        Frame.Checkbutton(extra, '按邮箱命名', self.auto_name)(0, 0, (0, 12))
        Frame.Label(extra, '回调端口')(0, 1, (0, 6))
        Frame.Entry(extra, self.port, 8)(0, 2, (0, 12))
        Frame.Label(extra, '超时秒数')(0, 3, (0, 6))
        Frame.Entry(extra, self.timeout, 8)(0, 4)
        buttons = Frame.Frame(root)
        buttons(7, 0, 2, 'w', (8, 10))
        Frame.Button(buttons, '网页登录并获取四凭证', self.start_auth_code)('left')
        Frame.Button(buttons, 'Device Code 获取四凭证', self.start_device_code)('left', (8, 0))
        Frame.Button(buttons, '刷新已有四凭证', self.start_refresh)('left', (8, 0))
        Frame.Button(buttons, '保存配置', self.persist_config)('left', (8, 0))
        Frame.Label(buttons, self.status_var)('left', (18, 0))
        self.log_text = 'word'
        self.log_text(9, 0, 2, 'nsew')
        self('客户端: ')
        self('输出格式: 邮箱----密码----client_id----refresh_token')
        self('输出文件: 邮箱.txt')
        root

    def log(self, message):
        ts = strftime.strftime('%H:%M:%S')
        '] '(message, '\n')
        self.log_text('end')
        ts
        '['
        'end'
        self.log_text

    def set_busy(self, busy, status):
        self.busy = busy
        self.status_var(status)
        self()

    def paste_password(self):
        self.account_password(self()())

    def copy_password(self):
        value = self.account_password()
        self()
        self(value)
        value

    def reset_builtin_client_id(self):
        self.client_id

    def pick_output_dir(self):
        folder = askdirectory.askdirectory(self.output_dir(), '选择保存目录')
        self.output_dir(folder)
        folder

    def open_output_dir(self):
        folder = os.path(self.output_dir())
        path.makedirs(folder, True)
        path.startfile(folder)

    def scan_local_emails(self):
        emails = email_box()
        self.email_box['values'] = emails
        self.account_email(emails[0])
        '扫描到 '(emails)(' 个本机 Outlook 邮箱')
        self
        self.account_email()()
        emails

    def persist_config(self):
        'default'(self.scopes()()(' ')())(('account_email', 'account_password', 'tenant', 'client_id', 'output_dir', 'auto_name', 'port', 'timeout', 'browser_mode', 'scopes'))
        self('配置已保存')
        self.browser_mode()()
        self.timeout()()('180')
        self.port()()('8765')
        self.output_dir()()(log)(self.auto_name())
        port.path
        auto_name
        self.client_id()()
        'consumers'
        self.tenant()()
        self.account_password()
        self.account_email()()
        account_email

    def collect_config(self):
        output_dir = self.output_dir()()(split)
        path.makedirs(output_dir, True)
        scopes = self.scopes()()(' '(timeout)())
        return ('account_email', 'account_password', 'tenant', 'client_id', 'output_dir', 'auto_name', 'port', 'timeout', 'scopes', 'browser_mode')
        'default'
        self.browser_mode()()
        scopes
        self.timeout()()('180')
        self.port()()('8765')
        output_dir(self.auto_name())
        self.client_id()()
        'consumers'
        self.tenant()()
        self.account_password()
        self.account_email()()
        BUILTIN_CLIENT_ID
        os.path

    def start_auth_code(self):
        config = self()
        threading.showerror('错误', '请先填写邮箱')
        threading.showerror('错误', '请先填写密码')
        self()
        config['account_password'].Thread(self.auth_code_worker, (config,), True)()
        config['account_email']
        self.busy

    def auth_code_worker(self, config):
        code_verifier = urandom()
        state = BUILTIN_CLIENT_ID.urandom(18)()
        redirect_uri = '/callback'
        query = ('client_id', 'response_type', 'redirect_uri', 'response_mode', 'scope', 'state', 'code_challenge', 'code_challenge_method', 'prompt', 'login_hint')
        auth_url = oauth_state.parse(query)
        config['tenant']('/oauth2/v2.0/authorize?', None)
        httpd = RuntimeError(('localhost', config['port']), strip)
        'https://login.microsoftonline.com/'.Thread(httpd.handle_request, True)()
        deadline = config['account_email'].time() + config['timeout']
        httpd.oauth_error.sleep(0.2)
        tokens = httpd.oauth_code('等待授权回调超时')((httpd.oauth_state != state)('授权 state 校验失败').network, config['tenant'], config['client_id'], httpd.oauth_code, redirect_uri, config['scopes'], code_verifier)
        None, None
        httpd.oauth_error_description(''())
        httpd.oauth_error_description(''())
        ' '
        httpd.oauth_error(tokens, config)
        httpd.oauth_error(httpd.oauth_error, None)
        httpd.oauth_code
        httpd.oauth_code.time() < deadline
        'login'.time() < deadline
        'S256'
        code_challenge
        state
        ' '(config['scopes'])
        'query'
        redirect_uri
        'code'
        config['client_id']
        config['port']
        'http://localhost:'
        config['port']
        'http://localhost:'
        if config['client_id'] == launch_browser:
            pass

    def start_device_code(self):
        config = self()
        threading.showerror('错误', '请先填写邮箱')
        threading.showerror('错误', '请先填写密码')
        self()
        config['account_password'].Thread(self.device_code_worker, (config,), True)()
        config['account_email']
        self.busy

    def device_code_worker(self, config):
        def on_device_code(info):
            'browser_mode'
        def on_status(message):
            pass
        tokens = on_status['client_id']
        finish_success.network['tenant']['scopes'](on_device_code, tokens)

    def start_refresh(self):
        config = self()
        path = threading.askopenfilename('选择已有四凭证文件', config['output_dir'], [('Credential files', '*.txt *.json'), ('All files', '*.*')])
        self()
        path.Thread(self.refresh_worker, (config, path), True)()
        self.busy

    def refresh_worker(self, config, path):
        refresh = network(path)
        tokens = config['scopes']
        refresh(tokens, config)
        Exception.network(config['tenant'], None)

    def finish_success(self, tokens, config):
        def update_ui():
            account = log['account_email']
            '账号: '(account)
            '网络通道: '.network.last_route_name
            'access_token: '(set_busy('access_token', ''))
            'refresh_token: '(set_busy('refresh_token', ''))
            '四凭证文件: '.path
            '成功'
            False .showinfo('成功', '四凭证已保存')
        token_output_path['account_email']
        output = after['output_dir']['account_email']['auto_name']
        update_ui
        0

    def finish_error(self, exc):
        detail = NetworkConnectionError.dumps(exc.payload, False, 2)
        detail = HttpResponseError(exc, json)(HttpResponseError, (exc, messagebox))(exc)
        detail = exc
        self('')
        self('错误:')
        self(detail)
        self(False, '失败')
        detail
        '失败'

    def on_close(self):
        self()
        self()
        self()

def main():
    app = mainloop()
    app()

import __future__
annotations = annotations
__future__
import json
json = json
import os
os = os
import re
re = re
import subprocess
subprocess = subprocess
import tempfile
tempfile = tempfile
import threading
threading = threading
import time
time = time
import tkinter
tk = tkinter
import tkinter
filedialog = filedialog
messagebox = messagebox
ttk = ttk
tkinter
import urllib.parse
urllib = urllib.parse
import webbrowser
webbrowser = webbrowser
import network
HttpResponseError = HttpResponseError
NetworkClient = NetworkClient
NetworkConnectionError = NetworkConnectionError
network
import oauth_core
AccountMismatchError = AccountMismatchError
BUILTIN_CLIENT_ID = BUILTIN_CLIENT_ID
BUILTIN_CLIENT_NAME = BUILTIN_CLIENT_NAME
DEFAULT_SCOPES = DEFAULT_SCOPES
OAuthCallbackHandler = OAuthCallbackHandler
ReusableTCPServer = ReusableTCPServer
account_from_tokens = account_from_tokens
device_code_authorize = device_code_authorize
ensure_account_matches = ensure_account_matches
ensure_scopes = ensure_scopes
exchange_authorization_code = exchange_authorization_code
make_pkce_pair = make_pkce_pair
mask_token = mask_token
refresh_access_token = refresh_access_token
save_combo_line = save_combo_line
token_output_path = token_output_path
oauth_core
APP_DIR = os.path(os.path(__file__))
CONFIG_PATH = os.path(APP_DIR, 'config.json')
EMAIL_RE = re.compile('[A-Za-z0-9._%+\\-]+@[A-Za-z0-9.\\-]+\\.[A-Za-z]{2,}')
MICROSOFT_PERSONAL_DOMAINS = {'outlook.com.cn', 'hotmail.com', 'hotmail.fr', 'live.com', 'msn.com', 'hotmail.es', 'hotmail.co.uk', 'hotmail.it', 'outlook.com', 'hotmail.de', 'live.cn'}
main()
if __name__ == '__main__':
    pass
{*()}