using System;
using System.Collections.Generic;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;

namespace CadStudio.SolidWorks.AddinHost
{
    /// <summary>
    /// Point &amp; Prompt 的 DTO 契约与 token 派生。
    ///
    /// 与 `docs/point-and-prompt/selection-context.schema.json` 保持字段一致；
    /// 文档 token 的算法必须与 `scripts/sw_selection_context.py::document_token` 相同，
    /// 否则 MCP 读到的上下文会被误判为 stale。
    /// </summary>
    internal static class PointPromptContract
    {
        internal const string SchemaVersion = "1.0";
        internal const string AddinVersion = "1.0.0";

        /// <summary>由文档路径与类型派生稳定 token（与 Python 侧算法一致）。</summary>
        internal static string DocumentToken(string path, string documentType)
        {
            string normalized = NormalizePath(path);
            string seed = string.IsNullOrEmpty(normalized)
                ? "untitled|" + (documentType ?? "Part")
                : normalized + "|" + (documentType ?? "Part");
            return "doc-" + Sha1Hex(seed).Substring(0, 24);
        }

        /// <summary>规范化路径：绝对化、统一反斜杠、小写（用于 token 派生）。</summary>
        internal static string NormalizePath(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return string.Empty;
            }
            string text = path.Trim().Trim('"');
            try
            {
                text = System.IO.Path.GetFullPath(text);
            }
            catch (Exception)
            {
                // 无效路径保留原样参与哈希，不抛异常。
            }
            return text.Replace('/', '\\').TrimEnd('\\').ToLowerInvariant();
        }

        /// <summary>实体 token：类型 + 所属特征 + 几何摘要。</summary>
        internal static string EntityToken(string entityType, string featureName, IDictionary<string, object> geometry)
        {
            string signature = GeometrySignature(geometry);
            string seed = (entityType ?? "Unknown").ToLowerInvariant()
                + "|" + (featureName ?? string.Empty).ToLowerInvariant()
                + "|" + signature;
            return "ent-" + Sha1Hex(seed).Substring(0, 24);
        }

        /// <summary>选择 token：文档 + 实体集合 + revision。</summary>
        internal static string SelectionToken(string documentToken, IEnumerable<string> entityTokens, int revision)
        {
            var sorted = new List<string>(entityTokens ?? new string[0]);
            sorted.Sort(StringComparer.Ordinal);
            string seed = (documentToken ?? string.Empty) + "|" + revision.ToString(CultureInfo.InvariantCulture) + "|" + string.Join(",", sorted);
            return "sel-" + Sha1Hex(seed).Substring(0, 24);
        }

        /// <summary>几何摘要：按 key 排序的稳定字符串，只保留 6 位小数。</summary>
        internal static string GeometrySignature(IDictionary<string, object> geometry)
        {
            if (geometry == null || geometry.Count == 0)
            {
                return "no-geometry";
            }
            var keys = new List<string>(geometry.Keys);
            keys.Sort(StringComparer.Ordinal);
            var builder = new StringBuilder();
            foreach (string key in keys)
            {
                builder.Append(key).Append('=').Append(FormatValue(geometry[key])).Append(';');
            }
            return Sha256Hex(builder.ToString()).Substring(0, 24);
        }

        private static string FormatValue(object value)
        {
            if (value == null)
            {
                return "null";
            }
            var list = value as System.Collections.IEnumerable;
            if (list != null && !(value is string))
            {
                var parts = new List<string>();
                foreach (object item in list)
                {
                    parts.Add(FormatValue(item));
                }
                return "[" + string.Join(",", parts) + "]";
            }
            double number;
            if (value is double || value is float || value is int || value is long || value is decimal)
            {
                number = Convert.ToDouble(value, CultureInfo.InvariantCulture);
                return Math.Round(number, 6).ToString("0.######", CultureInfo.InvariantCulture);
            }
            if (double.TryParse(Convert.ToString(value, CultureInfo.InvariantCulture), NumberStyles.Float, CultureInfo.InvariantCulture, out number))
            {
                return Math.Round(number, 6).ToString("0.######", CultureInfo.InvariantCulture);
            }
            return Convert.ToString(value, CultureInfo.InvariantCulture);
        }

        internal static string Sha1Hex(string text)
        {
            using (var sha = SHA1.Create())
            {
                byte[] bytes = sha.ComputeHash(Encoding.UTF8.GetBytes(text ?? string.Empty));
                return ToHex(bytes);
            }
        }

        internal static string Sha256Hex(string text)
        {
            using (var sha = SHA256.Create())
            {
                byte[] bytes = sha.ComputeHash(Encoding.UTF8.GetBytes(text ?? string.Empty));
                return ToHex(bytes);
            }
        }

        private static string ToHex(byte[] bytes)
        {
            var builder = new StringBuilder(bytes.Length * 2);
            foreach (byte item in bytes)
            {
                builder.Append(item.ToString("x2", CultureInfo.InvariantCulture));
            }
            return builder.ToString();
        }

        /// <summary>RFC 3339 UTC 时间戳。</summary>
        internal static string UtcNow()
        {
            return DateTime.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture);
        }

        /// <summary>把毫米数值四舍五入到 4 位小数（null 安全）。</summary>
        internal static double? RoundMm(double? value)
        {
            return value.HasValue ? (double?)Math.Round(value.Value, 4) : null;
        }

        /// <summary>把米转换为毫米。</summary>
        internal static double? MetresToMm(double? value)
        {
            return value.HasValue ? (double?)Math.Round(value.Value * 1000.0, 4) : null;
        }

        /// <summary>构建实体 DTO；与 SelectionContext Schema 对齐。</summary>
        internal static Dictionary<string, object> BuildEntity(
            string entityType,
            string geometryType,
            string displayName,
            IDictionary<string, object> geometry,
            string featureName,
            string featureType,
            double confidence,
            string resolutionStatus,
            string resolutionMethod,
            string resolutionReason,
            IList<string> evidence,
            string persistentReference)
        {
            var entity = new Dictionary<string, object>
            {
                { "entityToken", EntityToken(entityType, featureName, geometry) },
                { "entityType", entityType ?? "Unknown" },
                { "geometryType", string.IsNullOrEmpty(geometryType) ? "Unknown" : geometryType },
                { "resolution", new Dictionary<string, object>
                    {
                        { "status", resolutionStatus ?? "unresolved" },
                        { "method", resolutionMethod ?? "none" },
                        { "confidence", Math.Round(confidence, 3) },
                    }
                },
            };
            if (!string.IsNullOrEmpty(displayName))
            {
                entity["displayName"] = displayName;
            }
            if (geometry != null && geometry.Count > 0)
            {
                entity["approximateGeometry"] = geometry;
            }
            if (!string.IsNullOrEmpty(featureName))
            {
                entity["featureCandidates"] = new List<object>
                {
                    new Dictionary<string, object>
                    {
                        { "featureName", featureName },
                        { "featureType", string.IsNullOrEmpty(featureType) ? "Unknown" : featureType },
                        { "confidence", Math.Round(confidence <= 0 ? 0.5 : confidence, 3) },
                    },
                };
            }
            var resolution = (Dictionary<string, object>)entity["resolution"];
            if (!string.IsNullOrEmpty(resolutionReason))
            {
                resolution["reason"] = resolutionReason;
            }
            if (evidence != null && evidence.Count > 0)
            {
                resolution["evidence"] = new List<string>(evidence);
            }
            if (!string.IsNullOrEmpty(persistentReference))
            {
                entity["persistentReference"] = persistentReference;
            }
            return entity;
        }

        /// <summary>构建完整 SelectionContext DTO。</summary>
        internal static Dictionary<string, object> BuildContext(
            string documentToken,
            string path,
            string title,
            string documentType,
            string configuration,
            int documentRevision,
            int revision,
            bool dirty,
            string units,
            IList<Dictionary<string, object>> entities,
            string mode,
            string activeEntityToken,
            int bridgeRevision,
            string source)
        {
            var tokens = new List<string>();
            foreach (Dictionary<string, object> entity in entities ?? new List<Dictionary<string, object>>())
            {
                tokens.Add(Convert.ToString(entity["entityToken"], CultureInfo.InvariantCulture));
            }
            string selectionToken = SelectionToken(documentToken, tokens, revision);
            var selection = new Dictionary<string, object>
            {
                { "selectionToken", selectionToken },
                { "mode", string.IsNullOrEmpty(mode) ? "selected" : mode },
                { "objects", entities ?? new List<Dictionary<string, object>>() },
            };
            if (!string.IsNullOrEmpty(activeEntityToken))
            {
                selection["activeEntityToken"] = activeEntityToken;
            }
            if (mode == "pinned")
            {
                selection["pinnedAt"] = UtcNow();
            }
            return new Dictionary<string, object>
            {
                { "schemaVersion", SchemaVersion },
                {
                    "document", new Dictionary<string, object>
                    {
                        { "documentToken", documentToken },
                        { "path", path ?? string.Empty },
                        { "title", string.IsNullOrEmpty(title) ? "Untitled" : title },
                        { "documentType", string.IsNullOrEmpty(documentType) ? "Part" : documentType },
                        { "configuration", string.IsNullOrEmpty(configuration) ? "Default" : configuration },
                        { "documentRevision", documentRevision },
                    }
                },
                {
                    "modelState", new Dictionary<string, object>
                    {
                        { "revision", revision },
                        { "dirty", dirty },
                        { "units", string.IsNullOrEmpty(units) ? "UNKNOWN" : units },
                    }
                },
                { "selection", selection },
                {
                    "provenance", new Dictionary<string, object>
                    {
                        { "source", string.IsNullOrEmpty(source) ? "addin" : source },
                        { "capturedAt", UtcNow() },
                        { "hostVersion", string.Empty },
                        { "addinVersion", AddinVersion },
                        { "bridgeRevision", bridgeRevision },
                    }
                },
                { "staleness", new Dictionary<string, object> { { "status", "fresh" }, { "checkedAt", UtcNow() } } },
            };
        }
    }
}
