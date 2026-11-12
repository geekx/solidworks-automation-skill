using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.Windows.Forms;

namespace CadStudio.SolidWorks.AddinHost
{
    /// <summary>
    /// Point &amp; Prompt TaskPane：显示当前目标卡片、Prompt 输入与计划卡片。
    ///
    /// 只做本地展示与文件读写；不调用大模型，也不直接写模型。MCP 写入
    /// plan-previews/ 后由定时器轮询刷新。
    /// </summary>
    internal sealed class TaskPaneControl : UserControl
    {
        private readonly CadBridgeState bridge;
        private readonly SelectionManager selection;
        private readonly HighlightManager highlights;
        private readonly Action showPropertyPage;
        private readonly Action refreshDiagnostics;
        private readonly Action<string> log;

        private readonly Label documentLabel;
        private readonly Label targetLabel;
        private readonly Label candidateLabel;
        private readonly Label statusLabel;
        private readonly Label planLabel;
        private readonly TextBox promptBox;
        private readonly Timer refreshTimer;
        private string lastPlanSignature;

        internal TaskPaneControl(
            string revision,
            string diagnosticPath,
            CadBridgeState bridge,
            SelectionManager selection,
            Action showPropertyPage,
            Action refreshDiagnostics,
            Action<string> log)
        {
            this.bridge = bridge;
            this.selection = selection;
            this.showPropertyPage = showPropertyPage;
            this.refreshDiagnostics = refreshDiagnostics;
            this.log = log ?? delegate { };
            this.highlights = new HighlightManager(this.log);

            Dock = DockStyle.Fill;
            BackColor = Color.White;
            Padding = new Padding(10);

            var title = new Label
            {
                AutoSize = true,
                Font = new Font(SystemFonts.MessageBoxFont.FontFamily, 11f, FontStyle.Bold),
                Text = "Point & Prompt",
                Location = new Point(10, 8),
            };
            var subtitle = new Label
            {
                AutoSize = true,
                ForeColor = Color.DimGray,
                Text = "SolidWorks " + revision + " · " + diagnosticPath,
                Location = new Point(10, 30),
            };

            documentLabel = CreateLabel(new Point(10, 54), new Size(300, 40), "文档：未打开");
            targetLabel = CreateLabel(new Point(10, 96), new Size(300, 52), "目标：未选择");
            candidateLabel = CreateLabel(new Point(10, 150), new Size(300, 52), "候选特征：无");
            statusLabel = CreateLabel(new Point(10, 204), new Size(300, 40), "状态：空闲");
            statusLabel.ForeColor = Color.DarkGreen;

            promptBox = new TextBox
            {
                Multiline = true,
                ScrollBars = ScrollBars.Vertical,
                Size = new Size(280, 52),
                Location = new Point(10, 246),
                Text = "把这个孔改成 Ø8，中心位置不变",
            };

            var submitButton = new Button
            {
                Text = "提交 Prompt",
                Size = new Size(135, 28),
                Location = new Point(10, 304),
            };
            submitButton.Click += delegate { SubmitPrompt(); };

            var pinButton = new Button
            {
                Text = "Pin 当前选择",
                Size = new Size(135, 28),
                Location = new Point(155, 304),
            };
            pinButton.Click += delegate { PinSelection(); };

            var unpinButton = new Button
            {
                Text = "解除 Pin",
                Size = new Size(135, 28),
                Location = new Point(10, 338),
            };
            unpinButton.Click += delegate { Unpin(); };

            var refreshButton = new Button
            {
                Text = "刷新上下文",
                Size = new Size(135, 28),
                Location = new Point(155, 338),
            };
            refreshButton.Click += delegate { RefreshContext(); };

            planLabel = new Label
            {
                AutoSize = false,
                BorderStyle = BorderStyle.FixedSingle,
                BackColor = Color.FromArgb(248, 248, 248),
                Size = new Size(280, 120),
                Location = new Point(10, 374),
                Text = "计划卡片：等待 AI 计划",
                Padding = new Padding(6),
            };

            var propertyButton = new Button
            {
                Text = "打开参数页",
                Size = new Size(135, 28),
                Location = new Point(10, 502),
            };
            propertyButton.Click += delegate { showPropertyPage(); };
            var diagnosticButton = new Button
            {
                Text = "刷新诊断",
                Size = new Size(135, 28),
                Location = new Point(155, 502),
            };
            diagnosticButton.Click += delegate
            {
                refreshDiagnostics();
                statusLabel.Text = "状态：诊断已刷新";
            };

            Controls.Add(title);
            Controls.Add(subtitle);
            Controls.Add(documentLabel);
            Controls.Add(targetLabel);
            Controls.Add(candidateLabel);
            Controls.Add(statusLabel);
            Controls.Add(promptBox);
            Controls.Add(submitButton);
            Controls.Add(pinButton);
            Controls.Add(unpinButton);
            Controls.Add(refreshButton);
            Controls.Add(planLabel);
            Controls.Add(propertyButton);
            Controls.Add(diagnosticButton);

            refreshTimer = new Timer { Interval = 500 };
            refreshTimer.Tick += delegate { PollBridge(); };
            refreshTimer.Start();
            PollBridge();
        }

        private static Label CreateLabel(Point location, Size size, string text)
        {
            return new Label
            {
                AutoSize = false,
                Size = size,
                Location = location,
                Text = text,
                Font = new Font(SystemFonts.MessageBoxFont.FontFamily, 9f),
            };
        }

        private void SubmitPrompt()
        {
            string text = (promptBox.Text ?? string.Empty).Trim();
            if (text.Length == 0)
            {
                statusLabel.Text = "状态：Prompt 为空";
                return;
            }
            Dictionary<string, object> context = selection != null ? selection.CurrentContext : null;
            if (context == null)
            {
                context = bridge.ReadContext();
            }
            if (context == null)
            {
                statusLabel.Text = "状态：请先在图形区选择对象再提交";
                return;
            }
            string requestId = bridge.AppendPromptRequest(text, context);
            statusLabel.Text = "状态：已提交 " + requestId + "，等待计划";
            bridge.AppendHistory("prompt", new Dictionary<string, object> { { "requestId", requestId }, { "length", text.Length } });
        }

        private void PinSelection()
        {
            if (selection == null)
            {
                statusLabel.Text = "状态：选择管理器未就绪";
                return;
            }
            Dictionary<string, object> context = selection.RefreshSelection("pinned");
            if (context == null)
            {
                statusLabel.Text = "状态：没有可 Pin 的选择";
                return;
            }
            var document = (Dictionary<string, object>)context["document"];
            statusLabel.Text = "状态：已 Pin " + Convert.ToString(document["title"], CultureInfo.InvariantCulture);
            bridge.AppendHistory("pin", new Dictionary<string, object> { { "mode", "pinned" } });
        }

        private void Unpin()
        {
            bridge.SetPinned(null, null, null);
            bridge.AppendHistory("unpin", new Dictionary<string, object> { { "reason", "taskpane_button" } });
            statusLabel.Text = "状态：已解除 Pin";
        }

        private void RefreshContext()
        {
            if (selection != null)
            {
                selection.RefreshSelection("selected");
            }
            PollBridge(true);
            statusLabel.Text = "状态：上下文已刷新";
        }

        /// <summary>供 CommandManager 回调强制刷新卡片。</summary>
        internal void RefreshFromBridge()
        {
            PollBridge(true);
        }

        private void PollBridge(bool force = false)
        {
            try
            {
                Dictionary<string, object> context = bridge.ReadContext();
                if (context != null)
                {
                    UpdateDocumentCard(context);
                    UpdateTargetCard(context);
                }
                Dictionary<string, object> planPreview = bridge.ReadLatestPlanPreview();
                if (planPreview != null)
                {
                    string signature = Convert.ToString(planPreview["status"], CultureInfo.InvariantCulture) + "|"
                        + (planPreview.ContainsKey("plan") ? Convert.ToString(planPreview["plan"], CultureInfo.InvariantCulture).Length : 0);
                    if (force || signature != lastPlanSignature)
                    {
                        lastPlanSignature = signature;
                        UpdatePlanCard(planPreview);
                    }
                }
                ApplyHighlights();
            }
            catch (Exception exception)
            {
                statusLabel.Text = "状态：桥接读取失败 " + exception.GetType().Name;
            }
        }

        private void UpdateDocumentCard(Dictionary<string, object> context)
        {
            var document = context.ContainsKey("document") ? context["document"] as Dictionary<string, object> : null;
            var modelState = context.ContainsKey("modelState") ? context["modelState"] as Dictionary<string, object> : null;
            if (document == null)
            {
                return;
            }
            documentLabel.Text = "文档：" + Convert.ToString(document["title"], CultureInfo.InvariantCulture)
                + " [" + Convert.ToString(document["configuration"], CultureInfo.InvariantCulture) + "]"
                + "\r\nrevision " + Convert.ToString(modelState != null && modelState.ContainsKey("revision") ? modelState["revision"] : 0, CultureInfo.InvariantCulture)
                + " · " + Convert.ToString(modelState != null && modelState.ContainsKey("units") ? modelState["units"] : "UNKNOWN", CultureInfo.InvariantCulture);
        }

        private void UpdateTargetCard(Dictionary<string, object> context)
        {
            var selectionDto = context.ContainsKey("selection") ? context["selection"] as Dictionary<string, object> : null;
            if (selectionDto == null)
            {
                targetLabel.Text = "目标：未选择";
                return;
            }
            var objects = selectionDto.ContainsKey("objects") ? selectionDto["objects"] as System.Collections.IList : null;
            if (objects == null || objects.Count == 0)
            {
                targetLabel.Text = "目标：无（" + Convert.ToString(selectionDto["mode"], CultureInfo.InvariantCulture) + "）";
                candidateLabel.Text = "候选特征：无";
                return;
            }
            Dictionary<string, object> entity = objects[0] as Dictionary<string, object>;
            if (entity == null)
            {
                targetLabel.Text = "目标：无法解析";
                return;
            }
            string geometry = "Unknown";
            string diameter = string.Empty;
            if (entity.ContainsKey("approximateGeometry"))
            {
                var geometryDto = entity["approximateGeometry"] as Dictionary<string, object>;
                if (geometryDto != null)
                {
                    geometry = Convert.ToString(entity["geometryType"], CultureInfo.InvariantCulture);
                    if (geometryDto.ContainsKey("diameterMm") && geometryDto["diameterMm"] != null)
                    {
                        diameter = " Ø" + Convert.ToString(geometryDto["diameterMm"], CultureInfo.InvariantCulture) + " mm";
                    }
                }
            }
            else
            {
                geometry = Convert.ToString(entity["geometryType"], CultureInfo.InvariantCulture);
            }
            targetLabel.Text = "目标：" + Convert.ToString(entity["entityType"], CultureInfo.InvariantCulture)
                + " / " + geometry + diameter
                + "\r\n模式：" + Convert.ToString(selectionDto["mode"], CultureInfo.InvariantCulture)
                + " · 高亮：" + (selection != null && selection.HoverAvailable ? "可用" : "降级为点选");

            string candidate = "候选特征：无（解析未完成或导入实体）";
            if (entity.ContainsKey("featureCandidates"))
            {
                var candidates = entity["featureCandidates"] as System.Collections.IList;
                if (candidates != null && candidates.Count > 0)
                {
                    var best = candidates[0] as Dictionary<string, object>;
                    if (best != null)
                    {
                        candidate = "候选特征：" + Convert.ToString(best["featureName"], CultureInfo.InvariantCulture)
                            + " (" + Convert.ToString(best["featureType"], CultureInfo.InvariantCulture)
                            + ", " + Convert.ToString(best["confidence"], CultureInfo.InvariantCulture) + ")";
                    }
                }
            }
            candidateLabel.Text = candidate;
        }

        private void UpdatePlanCard(Dictionary<string, object> planPreview)
        {
            var plan = planPreview.ContainsKey("plan") ? planPreview["plan"] as Dictionary<string, object> : null;
            var preview = planPreview.ContainsKey("preview") ? planPreview["preview"] as Dictionary<string, object> : null;
            if (plan == null)
            {
                planLabel.Text = "计划卡片：等待 AI 计划";
                return;
            }
            string summary = plan.ContainsKey("summary") ? Convert.ToString(plan["summary"], CultureInfo.InvariantCulture) : "参数修改";
            string operation = "—";
            string diff = "—";
            if (plan.ContainsKey("operation"))
            {
                var operationDto = plan["operation"] as Dictionary<string, object>;
                if (operationDto != null)
                {
                    operation = Convert.ToString(operationDto["kind"], CultureInfo.InvariantCulture);
                    if (operationDto.ContainsKey("dimension"))
                    {
                        operation += " " + Convert.ToString(operationDto["dimension"], CultureInfo.InvariantCulture);
                    }
                    diff = Convert.ToString(operationDto["oldValueMm"], CultureInfo.InvariantCulture)
                        + " → " + Convert.ToString(operationDto["newValueMm"], CultureInfo.InvariantCulture) + " mm";
                }
            }
            string status = Convert.ToString(planPreview["status"], CultureInfo.InvariantCulture);
            string reason = string.Empty;
            if (preview != null && preview.ContainsKey("error_code") && preview["error_code"] != null)
            {
                reason = "\r\n" + Convert.ToString(preview["error_code"], CultureInfo.InvariantCulture);
            }
            planLabel.Text = summary + "\r\n" + operation + "\r\n" + diff
                + "\r\n风险 " + Convert.ToString(plan["risk"], CultureInfo.InvariantCulture)
                + " · 状态 " + status + reason;
        }

        private void ApplyHighlights()
        {
            if (selection == null)
            {
                return;
            }
            IList<string> tokens = bridge.ReadHighlightTokens();
            if (tokens.Count == 0)
            {
                return;
            }
            string signature = string.Join(",", tokens);
            if (signature == lastHighlightSignature)
            {
                return;
            }
            lastHighlightSignature = signature;
            int applied = highlights.Apply(tokens, selection);
            bridge.AppendHistory("highlight", new Dictionary<string, object> { { "requested", tokens.Count }, { "applied", applied } });
        }

        private string lastHighlightSignature;

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                refreshTimer.Stop();
                refreshTimer.Dispose();
            }
            base.Dispose(disposing);
        }
    }
}
