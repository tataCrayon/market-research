// 弹出面板：既是界面，也是**采集的执行现场**。
//
// ## 为什么采集放在面板里，不放在 service worker 里
//
// `pageCapture.saveAsMHTML` 返回的是一个**句柄**（Chrome 把 MHTML 写进了 profile 的
// blob storage），而不是内存里的一串字节。**跨进程的句柄在 service worker 里读不稳。**
// 面板是普通扩展页面 = 真渲染进程，那里有 `FileReader`，读得出来。
//
// 代价：**面板被关掉，采集就断了。** 所以界面上反复写着「别关这个面板」。

const $ = (id) => document.getElementById(id);

const HOST_REGISTER_HINT = "python ar.py host --register";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function setText(id, value) {
  $(id).textContent = String(value);
}

// 把结果画出来。⚠️ **只显示事实，不评价**：PRD N5 要求不评分不排名。
//
// 失败信息用 `<details>` 折起来：默认只露一行「在哪一层坏的」，
// 展开才看完整原因。理由很实际——上次那条 `could not be read…` 有一百多字，
// 摊在面板上把别的行全挤掉了，而人真正要先看到的是「失败 1 篇」。
function renderResult(result) {
  const box = $("result");
  box.textContent = "";
  $("resultSection").hidden = false;

  const head = result.saved > 0
    ? `已入库 ${result.saved} 篇${result.failed ? `，失败 ${result.failed} 篇` : ""}`
      + `（${Math.round((result.bytes || 0) / 1024)} KB）`
    : `已入库 0 篇${result.failed ? `，失败 ${result.failed} 篇` : ""}`;
  box.appendChild(el("div", "line " + (result.saved > 0 ? "good" : "bad"), head));
  // 主页单独报一句：它是「账号从哪来」的唯一入口，而它的失败和笔记的失败
  // 处理方式完全不同（笔记能靠 DOM 解析，主页非要 state 不可）。
  if (result.profiles) {
    box.appendChild(el("div", "line note", `其中主页 ${result.profiles} 个（粉丝/简介靠它）`));
  }

  // 失败一律折进 `<details>`：标题写「哪一层」，展开看原文。
  const failures = result.failures || [];
  if (failures.length) {
    const details = document.createElement("details");
    details.open = failures.length === 1;  // 只有一条时直接展开，省一次点击
    const summary = el("summary", "bad",
      failures.length === 1 ? `原因：${shortReason(failures[0])}` : `${failures.length} 条失败原因`);
    details.appendChild(summary);
    for (const failure of failures) {
      details.appendChild(el("pre", "", failure));
    }
    box.appendChild(details);
  }

  if (result.partialImages) {
    box.appendChild(el("div", "line note",
      `${result.partialImages} 篇有图没加载出来：采集前把图片栏从第一张滑到最后一张`));
  }
  if (result.withoutState) {
    box.appendChild(el("div", "line note",
      `${result.withoutState} 篇没读到页面数据（分享数/精确发布日会缺，这是预期内的）`));
  }
  if (result.stateTooLarge) {
    box.appendChild(el("div", "line note",
      `${result.stateTooLarge} 个页面数据超过上限，这次没带——主页会因此失败，笔记不受影响`));
  }
  if (result.needReload) {
    box.appendChild(el("div", "line note",
      `${result.needReload} 个标签未加载/已休眠：点一下那个标签让它重新加载再采`));
  }
  if (result.skipped) {
    box.appendChild(el("div", "line note",
      `${result.skipped} 个标签超出单次上限 ${AR.MAX_TABS}：分两批`));
  }
  // 读法写出来：下次再出「读不到」时，这条信息直接告诉你哪条路通、哪条不通。
  if (result.readMethod) {
    box.appendChild(el("div", "hint", `读存档方式：${result.readMethod}`));
  }
  // 入库日志折起来：它是给排查用的，不是给人每次都要读的。
  // 但**必须能拿到**——「入库了但校验报警」这类信息不能只存在于看不见的地方（E47）。
  if (result.logs && result.logs.length) {
    const details = document.createElement("details");
    details.appendChild(el("summary", "", `入库日志（${result.logs.length} 篇）`));
    details.appendChild(el("pre", "", result.logs.join("\n\n").slice(-2000)));
    box.appendChild(details);
  }
}

// 一句话说清「哪一层 + 第一句」。原文太长时不该占满面板。
function shortReason(failure) {
  const text = String(failure || "");
  const layer = (text.match(/^([^✗]+)✗/) || [])[1] || "";
  const body = text.replace(/^[^✗]*✗\s*/, "").split(/[，。；]/)[0];
  return [layer, body].filter(Boolean).join("✗ ") || text.slice(0, 60);
}

// 把结果同步到图标 badge。面板会被关掉，badge 是**关门之后还留着的那点记忆**。
async function setBadge(text, color, title) {
  try {
    await chrome.runtime.sendMessage({ type: "AR_BADGE", text, color, title });
  } catch (error) {
    // background 不在（极端情况）就算了：面板上的字已经说清楚了。
  }
}

async function refresh() {
  setText("today", "–");
  setText("notes", "–");
  setText("bodies", "–");
  const reply = await AR.readStatus();
  if (!reply.hostReady) {
    setText("sub", "本地宿主没连上");
    $("register").hidden = false;
    return;
  }
  $("register").hidden = true;
  if (!reply.ok || !reply.status) {
    setText("sub", "连上宿主了，但读不到库：" + (reply.hint || "未知原因"));
    return;
  }
  const status = reply.status;
  $("today").textContent = status.collected_today ?? "–";
  $("notes").textContent = status.notes ?? "–";
  $("bodies").textContent = status.with_body ?? "–";
  const quota = status.quota_notes || [];
  $("sub").textContent = `今日详情额度 ${quota[0] ?? 0}/${quota[1] ?? 30} · ${status.data_dir}`;
}

$("capture").addEventListener("click", async () => {
  const button = $("capture");
  button.disabled = true;
  $("progress").hidden = false;
  $("progress").textContent = "正在找笔记页…";
  setBadge("…", "busy", "正在保存…");
  try {
    const result = await AR.captureAll({
      onProgress: ({ index, total, noteId, done }) => {
        $("progress").textContent = done
          ? `已处理 ${index + 1}/${total} 篇（${noteId}）`
          : `正在处理 ${index + 1}/${total} 篇（${noteId || "…"}）—— ${AR.KEEP_PANEL_OPEN}`;
      },
    });
    renderResult(result);
    $("progress").hidden = true;
    const summary = result.saved > 0
      ? `已入库 ${result.saved} 篇${result.failed ? `，失败 ${result.failed} 篇` : ""}`
      : `已入库 0 篇${result.failed ? `，失败 ${result.failed} 篇` : ""}`;    const detail = [summary].concat(result.failures || []).join("\n");
    setBadge(result.saved > 0 ? `✓${result.saved}` : "!", result.saved > 0 ? "ok" : "err", detail);
    await refresh();
  } catch (error) {
    $("progress").hidden = true;
    const failure = "采集过程异常：" + String((error && error.message) || error);
    renderResult({ saved: 0, failed: 1, bytes: 0, failures: [failure] });
    setBadge("!", "err", failure);
  } finally {
    button.disabled = false;
  }
});

$("register").addEventListener("click", () => {
  const box = $("result");
  box.textContent = "";
  $("resultSection").hidden = false;
  box.appendChild(el("div", "line", "本地宿主没注册。在项目目录里跑一次（只需一次）："));
  box.appendChild(el("pre", "", `cd D:\\Projects\\MyProjects\\market-research\\account-research\n${HOST_REGISTER_HINT}`));
  box.appendChild(el("div", "line hint", "跑完点上面的按钮重试。"));
  box.appendChild(el("div", "line hint",
    "已经跑过还是不行：`python ar.py host --doctor` 会逐项告诉你哪一环断了。"));
});

refresh();
