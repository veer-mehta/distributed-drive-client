import json
import os
from textwrap import indent

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()
SCOPE = os.getenv("SCOPE")


def auth(creds):
    if creds and os.path.exists(creds):
        creds = Credentials.from_authorized_user_info(creds, [SCOPE])

    elif not creds:
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", [SCOPE])
        creds = flow.run_local_server(port=0)

    elif not creds.valid:
        try:
            creds.refresh(Request())
        except Exception:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", [SCOPE])
            creds = flow.run_local_server(port=0)

    return creds


class AccountManager:
    def __init__(self, config_path="./acc_cfg.json"):
        self.config_path = config_path
        self.accs = self._load_config()
        self.creds_list = []

    def _load_config(self):
        if os.path.exists(self.config_path):
            accs = json.load(open(self.config_path, "r"))
            for name in accs:
                accs[name]["creds"] = Credentials.from_authorized_user_info(
                    accs[name]["creds"], [SCOPE]
                )
            return accs
        return {}

    def _save_config(self):
        save_data = {
            name: {"creds": json.loads(acc["creds"].to_json())}
            for name, acc in self.accs.items()
        }
        json.dump(save_data, open(self.config_path, "w"), indent=4)

    def add_acc(self, acc_name):
        creds = auth(self.accs.get(acc_name, {}).get("creds"))

        if creds:
            self.accs[acc_name] = {"creds": creds}
            self._save_config()
            return True
        return False

    def del_acc(self, acc_name):
        if acc_name in self.accs.keys():
            self.accs.pop(acc_name)
            self._save_config()
            return True
        return False

    def get_accs_info(self):
        return self.accs
