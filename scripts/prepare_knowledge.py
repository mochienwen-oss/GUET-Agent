import os
import time
import zipfile
import io
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "backend", ".env"))

# =====================配置区=====================
TOKEN = os.environ.get("MINERU_API_TOKEN", "")
INPUT_DIR = r"D:\guet-agent-微调\guet数据文件\培养方案"   # pdf源文件夹
OUTPUT_MD_DIR = r"D:\guet-agent-微调\_md\培养方案"       #输出md文件夹
BATCH_SIZE = 30  #每一批次上传多少个pdf，建议20‑50，不要超过200
MODEL_VERSION = "vlm"
LANGUAGE = "ch"
API_BATCH_URL = "https://mineru.net/api/v4/file-urls/batch"
API_BATCH_RESULT = "https://mineru.net/api/v4/extract-results/batch/{batch_id}"
TIMEOUT_POLL = 900   #轮询总超时秒数
POLL_INTERVAL = 5
# ================================================

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {TOKEN}"
}



def get_all_pdf(input_folder: str):
    """遍历文件夹获取全部pdf绝对路径"""
    pdf_list = []
    for root, _, files in os.walk(input_folder):
        for fname in files:
            if fname.lower().endswith(".pdf"):
                fullpath = os.path.abspath(os.path.join(root, fname))
                pdf_list.append(fullpath)
    return pdf_list


def submit_one_batch(file_paths: list[str]):
    """提交一批文件，获取上传url列表、batch_id"""
    files_param = []
    for idx, fp in enumerate(file_paths):
        basename = os.path.basename(fp)
        files_param.append({
            "name": basename,
            "data_id": f"mineru_{idx}_{basename.replace(' ','_')}"
        })
    payload = {
        "files": files_param,
        "model_version": MODEL_VERSION,
        "language": LANGUAGE,
        "enable_table": True,
        "enable_formula": True
    }
    resp = requests.post(API_BATCH_URL, headers=headers, json=payload, timeout=40)
    if resp.status_code != 200:
        print(f"❌提交batch请求http错误 {resp.status_code} {resp.text}")
        return None
    j = resp.json()
    if j["code"] != 0:
        print(f"❌获取上传链接失败 {j['msg']}")
        return None
    batch_id = j["data"]["batch_id"]
    upload_urls = j["data"]["file_urls"]
    print(f"\n====新批次 batch_id={batch_id}，文件数量 {len(file_paths)}====")
    # PUT上传每个本地文件
    for i, up_url in enumerate(upload_urls):
        local_fp = file_paths[i]
        print(f"上传：{os.path.basename(local_fp)}")
        try:
            with open(local_fp, "rb") as f:
                put_r = requests.put(up_url, data=f, timeout=150)
            if put_r.status_code in (200,201):
                print(f"✅ {os.path.basename(local_fp)} 上传OSS成功")
            else:
                print(f"❌ {os.path.basename(local_fp)} PUT失败 status={put_r.status_code}")
        except FileNotFoundError:
            print(f"❌文件不存在 {local_fp}")
        except Exception as e:
            print(f"❌上传异常 {os.path.basename(local_fp)} : {str(e)}")
    return batch_id


def poll_batch(batch_id: str):
    """轮询直到全部完成/失败，返回任务结果列表"""
    start = time.time()
    url = API_BATCH_RESULT.format(batch_id=batch_id)
    while time.time() - start < TIMEOUT_POLL:
        r = requests.get(url, headers=headers, timeout=30)
        j = r.json()
        if j["code"] !=0:
            print(f"查询batch异常 {j['msg']}")
            time.sleep(POLL_INTERVAL)
            continue
        items = j["data"]["extract_result"]
        all_finish = True
        for it in items:
            fn = it["file_name"]
            st = it["state"]
            if st in ("pending","running","converting","waiting‑file"):
                print(f"⏳ {fn} 解析状态 {st}")
                all_finish = False
            elif st == "failed":
                print(f"❌ {fn} 解析失败 {it.get('err_msg','')}")
            elif st == "done":
                print(f"✅ {fn} 解析完成")
        if all_finish:
            return items
        time.sleep(POLL_INTERVAL)
    print(f"⚠️ batch {batch_id} 轮询超时！")
    return None


def save_md_from_result(task_item, out_dir:str):
    """下载zip，提取full.md，保存本地输出文件夹"""
    zip_url = task_item.get("full_zip_url")
    filename = task_item["file_name"]
    if not zip_url:
        print(f"跳过 {filename}，无zip下载链接")
        return
    try:
        zip_resp = requests.get(zip_url, timeout=120)
        bio = io.BytesIO(zip_resp.content)
        md_text = None
        with zipfile.ZipFile(bio) as zf:
            for name in zf.namelist():
                if name.endswith("full.md"):
                    with zf.open(name) as f:
                        md_text = f.read().decode("utf‑8", errors="ignore")
                    break
        if md_text is None:
            print(f"❌ {filename} zip包找不到full.md")
            return
        base_name,_ = os.path.splitext(filename)
        out_md_path = os.path.join(out_dir, base_name + ".md")
        os.makedirs(out_dir, exist_ok=True)
        with open(out_md_path,"w",encoding="utf‑8") as fw:
            fw.write(md_text)
        print(f"💾已保存md → {out_md_path}")
    except Exception as e:
        print(f"❌下载保存失败 {filename}: {str(e)}")


def main():
    pdf_files = get_all_pdf(INPUT_DIR)
    if len(pdf_files) == 0:
        print(f"{INPUT_DIR} 没有找到任何PDF文件")
        return
    print(f"一共找到 {len(pdf_files)} 个PDF文件，准备批量解析")
    #切片分批
    for offset in range(0, len(pdf_files), BATCH_SIZE):
        batch_slice = pdf_files[offset:offset+BATCH_SIZE]
        bid = submit_one_batch(batch_slice)
        if bid is None:
            continue
        res_items = poll_batch(bid)
        if res_items is None:
            continue
        for item in res_items:
            save_md_from_result(item, OUTPUT_MD_DIR)
    print("\n====全部批次处理结束====")
    print(f"md输出目录：{OUTPUT_MD_DIR}")


if __name__ == "__main__":
    main()
