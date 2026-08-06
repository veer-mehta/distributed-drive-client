import os

from account_manager import AccountManager
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from storage_manager import StorageManager

load_dotenv()


def repl():

    cur_dir = []
    run = True
    while run:
        match input(
            "\n\n1> manage accounts\t2> view fs tree\t\t3> upload files\t\t4> upload folders\t\t\n5> download file\t6> download folder\t7> delete file\t\t8> delete folder\n9> quit\n>> "
        ):
            case "1":
                print("\n\n")
                for i, a in enumerate(acc_mgr.get_accs_info()):
                    print(f"[{i}] {a}", end="\t\t")

                match input("\n1> add account\t\t2> del account\t\t3> back\n>> "):
                    case "1":
                        if acc_mgr.add_acc(input("new acc name: ")):
                            print("acc added")
                        else:
                            print("failed to add acc")
                    case "2":
                        if acc_mgr.del_acc(input("acc name: ")):
                            print("acc deleted")
                        else:
                            print("failed to delete acc")

            case "2":
                str_mgr.print_fs_tree()

            case "3":
                if str_mgr.upload_file(
                    input("enter local path: "), input("enter remote path: ")
                ):
                    print("upload successful")
                else:
                    print("upload failed")

            case "4":
                if str_mgr.upload_folder(
                    input("enter local path: "), input("enter remote path: ")
                ):
                    print("upload successful")
                else:
                    print("upload failed")

            case "5":
                if str_mgr.download_file(
                    input("enter local path: "), input("enter remote path: ")
                ):
                    print("download successful")
                else:
                    print("download failed")

            case "6":
                if str_mgr.download_folder(
                    input("enter local path: "), input("enter remote path: ")
                ):
                    print("download successful")
                else:
                    print("download failed")

            case "7":
                if str_mgr.delete_file(input("enter remote path: ")):
                    print("deletion successful")
                else:
                    print("deletion failed")

            case "8":
                if str_mgr.delete_folder(input("enter remote path: ")):
                    print("deletion successful")
                else:
                    print("deletion failed")

            case "9":
                print("quitting")
                run = False

            case _:
                print("invalid option")


if __name__ == "__main__":
    acc_mgr = AccountManager()
    str_mgr = StorageManager(acc_mgr.accs)

    if not acc_mgr.accs:
        print("no accounts found. please add one.")
        if not acc_mgr.add_acc(input("new acc name: ")):
            print("auth failed... exiting...")
            exit(1)

    repl()
