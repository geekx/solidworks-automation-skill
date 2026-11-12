using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace CadStudio.SolidWorks.AddinHost
{
    /// <summary>
    /// Point &amp; Prompt 桥接目录（Add-in ↔ MCP 文件 IPC）。
    ///
    /// 目录与文件语义见 `docs/point-and-prompt/interaction-spec.md` 第 2 节：
    /// 写入使用临时文件 + 原子替换，读取对半写文件重试；两侧都不传递 COM 对象。
    /// </summary>
    internal sealed class CadBridgeState
    {
        private const string ContextFile = "context.json";
        private const string StateFile = "state.json";
        private const string HistoryFile = "history.jsonl";
        private const string PromptRequestsFile = "prompt-requests.jsonl";
        private const string HighlightsFile = "highlights.json";
        private const string PlanPreviewDir = "plan-previews";

        private readonly object syncRoot = new object();
        private readonly JavaScriptSerializer serializer = new JavaScriptSerializer
        {
            MaxJsonLength = int.MaxValue,
            RecursionLimit = 64,
        };

        internal string RootDirectory { get; private set; }

        internal CadBridgeState()
        {
            string overrideDir = Environment.GetEnvironmentVariable("CADSTUDIO_POINT_PROMPT_DIR");
            if (!string.IsNullOrWhiteSpace(overrideDir))
            {
                RootDirectory = Path.GetFullPath(Environment.ExpandEnvironmentVariables(overrideDir.Trim()));
            }
            else
            {
                string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                RootDirectory = Path.Combine(local, "CAD Studio", "SolidWorksAddin", "point-and-prompt");
            }
            EnsureDirectory();
        }

        private void EnsureDirectory()
        {
            Directory.CreateDirectory(RootDirectory);
            Directory.CreateDirectory(Path.Combine(RootDirectory, PlanPreviewDir));
        }

        // ------------------------------------------------------------------ 上下文

        /// <summary>写入 SelectionContext 并递增 bridgeRevision。</summary>
        internal void WriteContext(Dictionary<string, object> context)
        {
            lock (syncRoot)
            {
                WriteJsonAtomic(ContextFile, context);
                Dictionary<string, object> state = ReadStateInternal();
                state["bridgeRevision"] = ToInt(state, "bridgeRevision") + 1;
                state["updatedAt"] = PointPromptContract.UtcNow();
                WriteJsonAtomic(StateFile, state);
            }
        }

        /// <summary>读取 SelectionContext；不存在返回 null。</summary>
        internal Dictionary<string, object> ReadContext()
        {
            return ReadJson(ContextFile) as Dictionary<string, object>;
        }

        /// <summary>删除上下文（文档关闭/失效）。</summary>
        internal void ClearContext()
        {
            lock (syncRoot)
            {
                try
                {
                    File.Delete(Path.Combine(RootDirectory, ContextFile));
                }
                catch (IOException)
                {
                }
                catch (UnauthorizedAccessException)
                {
                }
            }
        }

        // ------------------------------------------------------------------ 状态与 Pin

        /// <summary>记录/清除 Pin 段。</summary>
        internal void SetPinned(string documentToken, string selectionToken, IList<string> entityTokens)
        {
            lock (syncRoot)
            {
                Dictionary<string, object> state = ReadStateInternal();
                state["pinned"] = string.IsNullOrEmpty(documentToken) || string.IsNullOrEmpty(selectionToken)
                    ? null
                    : new Dictionary<string, object>
                    {
                        { "documentToken", documentToken },
                        { "selectionToken", selectionToken },
                        { "entityTokens", entityTokens ?? new List<string>() },
                        { "pinnedAt", PointPromptContract.UtcNow() },
                    };
                state["bridgeRevision"] = ToInt(state, "bridgeRevision") + 1;
                state["updatedAt"] = PointPromptContract.UtcNow();
                WriteJsonAtomic(StateFile, state);
            }
        }

        /// <summary>读取 Add-in 运行状态段。</summary>
        internal Dictionary<string, object> ReadState()
        {
            return ReadStateInternal();
        }

        /// <summary>更新 Add-in 状态（hover/status/lastError）。</summary>
        internal void UpdateAddinStatus(string status, string hoverStatus, string lastError)
        {
            lock (syncRoot)
            {
                Dictionary<string, object> state = ReadStateInternal();
                state["addin"] = new Dictionary<string, object>
                {
                    { "status", status ?? "unknown" },
                    { "updatedAt", PointPromptContract.UtcNow() },
                };
                state["hover"] = new Dictionary<string, object>
                {
                    { "status", hoverStatus ?? "unknown" },
                    { "updatedAt", PointPromptContract.UtcNow() },
                };
                if (!string.IsNullOrEmpty(lastError))
                {
                    state["lastError"] = lastError;
                }
                state["bridgeRevision"] = ToInt(state, "bridgeRevision") + 1;
                WriteJsonAtomic(StateFile, state);
            }
        }

        private Dictionary<string, object> ReadStateInternal()
        {
            var state = ReadJson(StateFile) as Dictionary<string, object>;
            if (state == null)
            {
                state = new Dictionary<string, object>();
            }
            if (!state.ContainsKey("schemaVersion"))
            {
                state["schemaVersion"] = PointPromptContract.SchemaVersion;
            }
            if (!state.ContainsKey("bridgeRevision"))
            {
                state["bridgeRevision"] = 0;
            }
            return state;
        }

        /// <summary>读取某文档的模型 revision（由 MCP 写执行器维护）。</summary>
        internal int DocumentRevision(string documentToken)
        {
            Dictionary<string, object> state = ReadStateInternal();
            var revisions = state.ContainsKey("documentRevisions") ? state["documentRevisions"] as Dictionary<string, object> : null;
            if (revisions == null || !revisions.ContainsKey(documentToken))
            {
                return 0;
            }
            return ToInt(revisions, documentToken);
        }

        // ------------------------------------------------------------------ 历史 / Prompt / 计划 / 高亮

        /// <summary>追加交互历史。</summary>
        internal void AppendHistory(string kind, IDictionary<string, object> fields)
        {
            var record = new Dictionary<string, object>
            {
                { "at", PointPromptContract.UtcNow() },
                { "kind", kind },
            };
            if (fields != null)
            {
                foreach (KeyValuePair<string, object> pair in fields)
                {
                    record[pair.Key] = pair.Value;
                }
            }
            AppendJsonLine(HistoryFile, record);
        }

        /// <summary>记录 TaskPane 的 Prompt 请求（只写文件，不调用大模型）。</summary>
        internal string AppendPromptRequest(string prompt, Dictionary<string, object> context)
        {
            string requestId = "req-" + DateTime.UtcNow.Ticks.ToString(CultureInfo.InvariantCulture);
            var document = context != null && context.ContainsKey("document") ? context["document"] as Dictionary<string, object> : null;
            var selection = context != null && context.ContainsKey("selection") ? context["selection"] as Dictionary<string, object> : null;
            var modelState = context != null && context.ContainsKey("modelState") ? context["modelState"] as Dictionary<string, object> : null;
            var record = new Dictionary<string, object>
            {
                { "requestId", requestId },
                { "at", PointPromptContract.UtcNow() },
                { "prompt", prompt ?? string.Empty },
                {
                    "context", new Dictionary<string, object>
                    {
                        { "documentToken", document != null && document.ContainsKey("documentToken") ? document["documentToken"] : null },
                        { "selectionToken", selection != null && selection.ContainsKey("selectionToken") ? selection["selectionToken"] : null },
                        { "entityToken", selection != null && selection.ContainsKey("activeEntityToken") ? selection["activeEntityToken"] : null },
                        { "revision", modelState != null && modelState.ContainsKey("revision") ? modelState["revision"] : null },
                    }
                },
                { "status", "pending" },
            };
            AppendJsonLine(PromptRequestsFile, record);
            return requestId;
        }

        /// <summary>读取最新的计划卡片（MCP 写入 plan-previews）。</summary>
        internal Dictionary<string, object> ReadLatestPlanPreview()
        {
            try
            {
                string directory = Path.Combine(RootDirectory, PlanPreviewDir);
                var files = new DirectoryInfo(directory).GetFiles("*.json");
                if (files.Length == 0)
                {
                    return null;
                }
                Array.Sort(files, delegate (FileInfo left, FileInfo right) { return right.LastWriteTimeUtc.CompareTo(left.LastWriteTimeUtc); });
                return ReadJsonText(File.ReadAllText(files[0].FullName)) as Dictionary<string, object>;
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>读取高亮请求的实体 token 列表。</summary>
        internal IList<string> ReadHighlightTokens()
        {
            var payload = ReadJson(HighlightsFile) as Dictionary<string, object>;
            var tokens = new List<string>();
            if (payload == null || !payload.ContainsKey("entityTokens"))
            {
                return tokens;
            }
            var raw = payload["entityTokens"] as IEnumerable;
            if (raw == null)
            {
                return tokens;
            }
            foreach (object item in raw)
            {
                if (item != null)
                {
                    tokens.Add(Convert.ToString(item, CultureInfo.InvariantCulture));
                }
            }
            return tokens;
        }

        // ------------------------------------------------------------------ JSON 基础

        private object ReadJson(string fileName)
        {
            string path = Path.Combine(RootDirectory, fileName);
            for (int attempt = 0; attempt < 3; attempt++)
            {
                try
                {
                    if (!File.Exists(path))
                    {
                        return null;
                    }
                    string text = File.ReadAllText(path, Encoding.UTF8);
                    if (string.IsNullOrWhiteSpace(text))
                    {
                        throw new InvalidDataException("empty json file");
                    }
                    return serializer.DeserializeObject(text);
                }
                catch (IOException)
                {
                    Thread.Sleep(50 * (attempt + 1));
                }
                catch (InvalidDataException)
                {
                    Thread.Sleep(50 * (attempt + 1));
                }
                catch (ArgumentException)
                {
                    Thread.Sleep(50 * (attempt + 1));
                }
            }
            return null;
        }

        private object ReadJsonText(string text)
        {
            try
            {
                return serializer.DeserializeObject(text);
            }
            catch (Exception)
            {
                return null;
            }
        }

        private void WriteJsonAtomic(string fileName, object payload)
        {
            string path = Path.Combine(RootDirectory, fileName);
            string text = serializer.Serialize(payload);
            WriteTextAtomic(path, text + "\n");
        }

        private void AppendJsonLine(string fileName, object payload)
        {
            string path = Path.Combine(RootDirectory, fileName);
            string line = serializer.Serialize(payload) + "\n";
            lock (syncRoot)
            {
                EnsureDirectory();
                for (int attempt = 0; attempt < 3; attempt++)
                {
                    try
                    {
                        File.AppendAllText(path, line, Encoding.UTF8);
                        return;
                    }
                    catch (IOException)
                    {
                        Thread.Sleep(50 * (attempt + 1));
                    }
                }
            }
        }

        private void WriteTextAtomic(string path, string text)
        {
            lock (syncRoot)
            {
                EnsureDirectory();
                string temp = path + "." + Path.GetRandomFileName() + ".tmp";
                File.WriteAllText(temp, text, Encoding.UTF8);
                for (int attempt = 0; attempt < 5; attempt++)
                {
                    try
                    {
                        if (File.Exists(path))
                        {
                            File.Replace(temp, path, null);
                        }
                        else
                        {
                            File.Move(temp, path);
                        }
                        return;
                    }
                    catch (IOException)
                    {
                        Thread.Sleep(30 * (attempt + 1));
                    }
                    catch (UnauthorizedAccessException)
                    {
                        Thread.Sleep(30 * (attempt + 1));
                    }
                }
                try
                {
                    File.Copy(temp, path, true);
                }
                finally
                {
                    try
                    {
                        File.Delete(temp);
                    }
                    catch (Exception)
                    {
                    }
                }
            }
        }

        private static int ToInt(IDictionary<string, object> dictionary, string key)
        {
            if (dictionary == null || !dictionary.ContainsKey(key) || dictionary[key] == null)
            {
                return 0;
            }
            int value;
            if (int.TryParse(Convert.ToString(dictionary[key], CultureInfo.InvariantCulture), NumberStyles.Integer, CultureInfo.InvariantCulture, out value))
            {
                return value;
            }
            double number;
            if (double.TryParse(Convert.ToString(dictionary[key], CultureInfo.InvariantCulture), NumberStyles.Float, CultureInfo.InvariantCulture, out number))
            {
                return (int)number;
            }
            return 0;
        }
    }
}
