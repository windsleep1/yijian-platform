/**
 * agent-browser 初始化脚本：捕获页面发起的"文本文件下载"。
 *
 * ## 为什么需要它
 *
 * 本机环境的 agent-browser 抓不住真实下载 —— `agent-browser download <sel> <path>`
 * 一律返回 `Download was canceled`，连一个最朴素的 `data:` URL 锚点下载也失败
 * （换 URL 会报 `os error 10060`）。这是工具/环境侧的限制，不是被测应用的问题。
 *
 * 所以改用"拦截并留存"：`apps/admin` 的导出走的是
 * `new Blob([...])` → `URL.createObjectURL` → `<a download>` → `a.click()` 这条标准链路。
 * 这里把 `createObjectURL` 与 `HTMLAnchorElement.prototype.click` 各包一层，
 * 在页面里留下：
 *
 *   window.__ybBlobText      导出的**文本**（注意：`Blob.text()` 会吃掉 BOM）
 *   window.__ybBlobType      Blob MIME
 *   window.__ybBlobSize      Blob 字节数
 *   window.__ybBlobHead      前 4 个原始字节（用来确认 BOM = [239,187,191]）
 *   window.__ybDownloadName  <a download="..."> 的文件名
 *
 * 于是既验证了"内容对不对"，也验证了"浏览器确实被要求存成什么文件名"。
 *
 * 用法：
 *   agent-browser --init-script tools/local-verify/ab-capture-download.js open <url>
 *   ...点击下载按钮...
 *   agent-browser eval "window.__ybBlobText"
 */
(() => {
  const origCreate = URL.createObjectURL.bind(URL);
  window.__ybBlobText = null;
  window.__ybBlobType = null;
  window.__ybBlobSize = null;
  window.__ybDownloadName = null;

  URL.createObjectURL = function (blob) {
    const url = origCreate(blob);
    try {
      window.__ybBlobType = blob.type;
      window.__ybBlobSize = blob.size;
      blob.text().then((t) => {
        window.__ybBlobText = t;
      });
      // ⚠️ 必须另存原始字节：`Blob.text()` 走的是 "UTF-8 decode"，
      // 按规范会**吃掉开头的 BOM**。只看 text() 会误判成"没写 BOM"，
      // 而 Excel 能不能正确识别中文全看那三个字节（EF BB BF）。
      blob.arrayBuffer().then((buf) => {
        const bytes = new Uint8Array(buf);
        window.__ybBlobB64 = btoa(String.fromCharCode(...bytes.slice(0, 16)));
        window.__ybBlobHead = [...bytes.slice(0, 4)];
        window.__ybBlobNonAsciiTail = [...bytes.slice(-8)];
      });
    } catch (e) {
      /* 非 Blob 入参：忽略 */
    }
    return url;
  };

  const origClick = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () {
    try {
      if (this.download) window.__ybDownloadName = this.download;
    } catch (e) {
      /* ignore */
    }
    return origClick.apply(this, arguments);
  };
})();
