import concurrent.futures
import io
import json
import os
import time

from cryptography.fernet import Fernet
from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

load_dotenv()
KEY_FILE = str(os.getenv("KEY_FILE"))
BLOCK_SIZE = int(eval(os.getenv("BLOCK_SIZE", "5 * 1024 * 1024")))
THREADS_PER_ACCOUNT = int(os.getenv("THREADS_PER_ACCOUNT", 5))


class StorageManager:
    def __init__(self, accs, fs_path="fs.json"):
        self.cipher = self._get_cipher()
        self.fs_path = fs_path
        self.fs = self._load_json(self.fs_path)
        self.cached_workers = {}
        self.accs = accs

    def _get_cipher(self):
        if os.path.exists(KEY_FILE):
            key = open(KEY_FILE, "rb").read()
        else:
            key = Fernet.generate_key()
            open(KEY_FILE, "wb").write(key)
        return Fernet(key)

    def _load_json(self, path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception as e:
            print("warning: failed to load json with error: ", e)
            print("creating new fs.json")
            fs = {"root": {"type": "dir", "children": {}}}
            self._save_json(fs)
            return fs

    def _save_json(self, data):
        with open(self.fs_path, "w") as f:
            json.dump(data, f, indent=4)

    def _get_service(self, creds, acc_name):
        if acc_name not in self.cached_workers:
            self.cached_workers[acc_name] = build("drive", "v3", credentials=creds)
        return self.cached_workers[acc_name]

    def _upload_chunk_task(self, creds, local_path, byte_offset, remote_name, chunk_idx, acc_name):
        try:
            with open(local_path, "rb") as f:
                f.seek(byte_offset)
                chunk_data = f.read(BLOCK_SIZE)

            encrypted_chunk = self.cipher.encrypt(chunk_data)

            service = self._get_service(creds, acc_name)
            file_metadata = {
                "name": f"{remote_name}.chunk{chunk_idx}",
                "appProperties": {"type": "chunk", "order": str(chunk_idx)},
            }

            media = MediaIoBaseUpload(
                io.BytesIO(encrypted_chunk),
                mimetype="application/octet-stream",
                resumable=False,
            )
            drive_file = (
                service.files()
                .create(body=file_metadata, media_body=media, fields="id")
                .execute()
            )

            return {
                "drive_id": drive_file.get("id"),
                "idx": str(chunk_idx),
                "acc_name": acc_name,
            }
        except Exception as e:
            print(f"failed: chunk {chunk_idx}: {e}")
            return None

    def upload_file(self, local_path, remote_path):
        if not self.accs:
            return False

        if not remote_path or remote_path[-1] == "/":
            remote_path += local_path
        if remote_path[0] != "/":
            remote_path = "/" + remote_path

        num_accs = len(self.accs)
        file_size = os.path.getsize(local_path)
        total_chunks = (file_size + BLOCK_SIZE - 1) // BLOCK_SIZE
        remote_dir, remote_name = remote_path.rsplit("/", 1)

        print(f"{remote_name}: {total_chunks} chunks")
        chunks_info = [(None, None)] * total_chunks

        workers = num_accs * THREADS_PER_ACCOUNT
        completed = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            upload_tasks = []

            acc_names = list(self.accs.keys())

            for chunk_idx in range(total_chunks):
                acc_name = acc_names[chunk_idx % num_accs]
                creds = self.accs[acc_name]["creds"]

                byte_offset = chunk_idx * BLOCK_SIZE

                upload_tasks.append(executor.submit(self._upload_chunk_task, creds, local_path, byte_offset, remote_name, chunk_idx, acc_name))

            for future in concurrent.futures.as_completed(upload_tasks):
                res = future.result()
                if res:
                    chunks_info[int(res["idx"])] = (res["drive_id"], res["acc_name"])
                    completed += 1
                    print(completed, "/", total_chunks, "uploaded")

        if None in chunks_info:
            print("some chunks failed")
            return False

        node = self.fs["root"]
        for dir in filter(None, remote_dir.split("/")):
            node = node["children"].setdefault(dir, {"type": "dir", "children": {}})

        node["children"][remote_name] = {
            "type": "file",
            "name": remote_name,
            "size": file_size,
            "chunks": chunks_info,
            "timestamp": time.time(),
        }

        self._save_json(self.fs)
        return True

    def upload_folder(self, local_path, remote_path):
        if not remote_path:
            remote_path = local_path
        if remote_path[0] != "/":
            remote_path = "/" + remote_path

        for name in os.listdir(local_path):
            path = os.path.join(local_path, name)
            print(local_path, name, path, remote_path)

            if os.path.isfile(path):
                self.upload_file(path, os.path.join(remote_path, name))
            else:
                self.upload_folder(path, os.path.join(remote_path, name))

        return True

    def _download_chunk_task(self, creds, chunk_idx, drive_id, acc_name):
        try:
            service = self._get_service(creds, acc_name)
            request = service.files().get_media(fileId=drive_id)

            chunk_buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(chunk_buffer, request)
            done = False
            while done is False:
                _, done = downloader.next_chunk()

            decr_data = self.cipher.decrypt(chunk_buffer.getvalue())
            return {"idx": chunk_idx, "data": decr_data}

        except Exception as e:
            print(f"failed: chunk {chunk_idx}: {e}")
            return None

    def download_file(self, local_path, remote_path):

        if not local_path:
            local_path = remote_path
        if remote_path[0] != "/":
            remote_path = "/" + remote_path

        remote_dir, remote_file = remote_path.rsplit("/", 1)

        node = self.fs["root"]
        for dir in filter(None, remote_dir.split("/")):
            if dir not in node["children"]:
                return False

            node = node["children"][dir]

        file = node["children"][remote_file]
        if not file:
            return False

        num_chunks = len(file["chunks"])
        print(f"fetching {num_chunks} chunks({file['size']})")

        chunk_data_map = [b""] * num_chunks
        completed = 0
        workers = THREADS_PER_ACCOUNT * len(self.accs)

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                download_tasks = []

                for chunk_idx in range(num_chunks):
                    chunk = file["chunks"][chunk_idx]
                    creds = self.accs[chunk[1]]["creds"]
                    download_tasks.append(
                        executor.submit(
                            self._download_chunk_task,
                            creds,
                            chunk_idx,
                            chunk[0],
                            chunk[1],
                        )
                    )

                for future in concurrent.futures.as_completed(download_tasks):
                    res = future.result()
                    if res:
                        chunk_data_map[res["idx"]] = res["data"]
                        completed += 1
                        print(completed, "/", num_chunks, "downloaded")

            if None in chunk_data_map:
                print("failed to retrieve all chunks")
                return False

            with open(local_path, "wb") as output_f:
                for data in chunk_data_map:
                    output_f.write(data)

            print("reassembled successfully")
            return True

        except Exception as e:
            print(f"error downloading: {e}")
            return False

    def download_folder(self, local_path, remote_path):
        if not local_path:
            local_path = remote_path
        if remote_path[0] != "/":
            remote_path = "/" + remote_path

        node = self.fs["root"]
        for dir in filter(None, remote_path.split("/")):
            if dir not in node["children"]:
                return False
            node = node["children"][dir]

        dir = node["children"]
        os.makedirs(local_path, exist_ok=True)

        for name in dir:
            path = os.path.join(remote_path, name)
            print(local_path, name, path, remote_path)

            if dir[name]["type"] == "file":
                self.download_file(os.path.join(local_path, name), path)
            else:
                self.download_folder(os.path.join(local_path, name), path)

        return True

    def _delete_chunk_task(self, creds, drive_id, chunk_idx, acc_name):
        try:
            service = self._get_service(creds, acc_name)
            service.files().delete(fileId=drive_id).execute()
            return chunk_idx
        except Exception:
            return None

    def delete_file(self, remote_path):

        if remote_path == "":
            return False
        if remote_path[0] != "/":
            remote_path = "/" + remote_path

        remote_dir, remote_file = remote_path.rsplit("/", 1)

        node = self.fs["root"]
        for dir in filter(None, remote_dir.split("/")):
            if dir not in node["children"]:
                return False

            node = node["children"][dir]

        if remote_file not in node["children"]:
            return False

        parent = node
        node = node["children"][remote_file]

        if node["type"] != "file":
            return False

        chunks = node["chunks"]
        num_chunks = len(chunks)

        print(f"deleting {num_chunks} chunks from drive...")
        completed = 0
        workers = THREADS_PER_ACCOUNT * len(self.accs)

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            delete_tasks = []
            for chunk in chunks:
                acc_name = chunk[1]
                if acc_name in self.accs:
                    creds = self.accs[acc_name]["creds"]
                    delete_tasks.append(
                        executor.submit(
                            self._delete_chunk_task,
                            creds,
                            chunk[0],
                            chunk[1],
                            acc_name,
                        )
                    )

            for future in concurrent.futures.as_completed(delete_tasks):
                res = future.result()
                print(res)
                if res is not None:
                    completed += 1
                    print(completed, "/", num_chunks, "deleted")

        del parent["children"][remote_file]

        self._save_json(self.fs)
        return True

    def delete_folder(self, remote_path):

        if remote_path == "":
            return False
        if remote_path[0] != "/":
            remote_path = "/" + remote_path

        remote_dir, folder_name = remote_path.rsplit("/", 1)
        node = self.fs["root"]

        for dir in filter(None, remote_dir.split("/")):
            if dir not in node["children"]:
                return False

            node = node["children"][dir]

        if folder_name not in node["children"]:
            return False

        parent = node
        node = node["children"][folder_name]

        if node["type"] != "dir":
            return False

        dir = list(node["children"].items())

        for name, child in dir:
            path = f"{remote_path}/{name}"

            if child["type"] == "file":
                self.delete_file(path)
            else:
                self.delete_folder(path)

        del parent["children"][folder_name]
        self._save_json(self.fs)

        return True

    def print_fs_tree(self):
        def walk(node, prefix=""):
            children = list(node["children"].items())

            for i, (name, child) in enumerate(children):
                is_last = i == len(children) - 1

                print(prefix + ("└── " if is_last else "├── ") + name)

                if child["type"] == "dir":
                    walk(child, prefix + ("    " if is_last else "│   "))

        print("\nroot")
        walk(self.fs["root"])
