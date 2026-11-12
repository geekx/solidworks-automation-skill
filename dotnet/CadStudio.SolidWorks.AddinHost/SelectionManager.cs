using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;

namespace CadStudio.SolidWorks.AddinHost
{
    /// <summary>
    /// 图形区选择与 Pin 管理：订阅文档级选择事件，产出 SelectionContext 并写入桥接目录。
    ///
    /// COM 约束：所有成员只在 SolidWorks 主线程调用；不把 COM RCW 写入 JSON。
    /// </summary>
    internal sealed class SelectionManager : IDisposable
    {
        private readonly ISldWorks swApp;
        private readonly CadBridgeState bridge;
        private readonly Action<string> log;
        private readonly Dictionary<string, object> entityCache = new Dictionary<string, object>(StringComparer.Ordinal);
        private readonly Stopwatch throttle = Stopwatch.StartNew();
        private readonly HoverMonitor hover = new HoverMonitor();

        private ModelDoc2 attachedModel;
        private PartDoc attachedPart;
        private AssemblyDoc attachedAssembly;
        private DrawingDoc attachedDrawing;
        private Dictionary<string, object> currentContext;

        internal SelectionManager(ISldWorks swApp, CadBridgeState bridge, Action<string> log)
        {
            this.swApp = swApp;
            this.bridge = bridge;
            this.log = log ?? delegate { };
        }

        internal Dictionary<string, object> CurrentContext
        {
            get { return currentContext; }
        }

        internal bool HoverAvailable { get; private set; }

        // ------------------------------------------------------------------ 文档事件

        /// <summary>切换活动文档后重新订阅；旧文档订阅全部解除。</summary>
        internal void AttachActiveDocument()
        {
            DetachDocumentEvents();
            object active = swApp.ActiveDoc;
            if (active == null)
            {
                currentContext = null;
                entityCache.Clear();
                bridge.ClearContext();
                return;
            }

            attachedModel = active as ModelDoc2;
            if (attachedModel == null)
            {
                log("active document is not a ModelDoc2");
                return;
            }

            try
            {
                attachedPart = attachedModel as PartDoc;
                if (attachedPart != null)
                {
                    attachedPart.NewSelectionNotify += OnNewSelection;
                    attachedPart.ClearSelectionNotify += OnClearSelection;
                    attachedPart.DynamicHighlightNotify += OnDynamicHighlight;
                    attachedPart.DestroyNotify += OnDocumentDestroy;
                    HoverAvailable = true;
                }
                attachedAssembly = attachedModel as AssemblyDoc;
                if (attachedAssembly != null)
                {
                    attachedAssembly.NewSelectionNotify += OnNewSelection;
                    attachedAssembly.ClearSelectionNotify += OnClearSelection;
                    attachedAssembly.DynamicHighlightNotify += OnDynamicHighlight;
                    attachedAssembly.DestroyNotify += OnDocumentDestroy;
                    HoverAvailable = true;
                }
                attachedDrawing = attachedModel as DrawingDoc;
                if (attachedDrawing != null)
                {
                    attachedDrawing.NewSelectionNotify += OnNewSelection;
                    attachedDrawing.ClearSelectionNotify += OnClearSelection;
                    attachedDrawing.DynamicHighlightNotify += OnDynamicHighlight;
                    attachedDrawing.DestroyNotify += OnDocumentDestroy;
                    HoverAvailable = true;
                }
                entityCache.Clear();
                RefreshSelection("selected");
                bridge.UpdateAddinStatus("connected", HoverAvailable ? "available" : "unavailable", string.Empty);
            }
            catch (Exception exception)
            {
                HoverAvailable = false;
                log("document event subscription failed: " + exception.GetType().Name + ": " + exception.Message);
                bridge.UpdateAddinStatus("connected", "unavailable", exception.Message);
            }
        }

        /// <summary>解除当前文档的全部事件订阅。</summary>
        internal void DetachDocumentEvents()
        {
            try
            {
                if (attachedPart != null)
                {
                    attachedPart.NewSelectionNotify -= OnNewSelection;
                    attachedPart.ClearSelectionNotify -= OnClearSelection;
                    attachedPart.DynamicHighlightNotify -= OnDynamicHighlight;
                    attachedPart.DestroyNotify -= OnDocumentDestroy;
                }
                if (attachedAssembly != null)
                {
                    attachedAssembly.NewSelectionNotify -= OnNewSelection;
                    attachedAssembly.ClearSelectionNotify -= OnClearSelection;
                    attachedAssembly.DynamicHighlightNotify -= OnDynamicHighlight;
                    attachedAssembly.DestroyNotify -= OnDocumentDestroy;
                }
                if (attachedDrawing != null)
                {
                    attachedDrawing.NewSelectionNotify -= OnNewSelection;
                    attachedDrawing.ClearSelectionNotify -= OnClearSelection;
                    attachedDrawing.DynamicHighlightNotify -= OnDynamicHighlight;
                    attachedDrawing.DestroyNotify -= OnDocumentDestroy;
                }
            }
            catch (Exception exception)
            {
                log("detach document events: " + exception.Message);
            }
            attachedPart = null;
            attachedAssembly = null;
            attachedDrawing = null;
            attachedModel = null;
        }

        private int OnNewSelection()
        {
            // 选择事件高频触发：节流后再采集，避免阻塞主线程。
            if (throttle.ElapsedMilliseconds < 150)
            {
                return 0;
            }
            throttle.Restart();
            RefreshSelection("selected");
            return 0;
        }

        private int OnClearSelection()
        {
            entityCache.Clear();
            bridge.ClearContext();
            bridge.SetPinned(null, null, null);
            bridge.AppendHistory("unpin", new Dictionary<string, object> { { "reason", "clear_selection" } });
            currentContext = null;
            return 0;
        }

        private int OnDocumentDestroy()
        {
            entityCache.Clear();
            bridge.ClearContext();
            bridge.SetPinned(null, null, null);
            bridge.AppendHistory("unpin", new Dictionary<string, object> { { "reason", "document_closed" } });
            currentContext = null;
            return 0;
        }

        // ------------------------------------------------------------------ Hover

        /// <summary>动态高亮回调：只更新 hover 预览，不改变已 Pin 的目标。</summary>
        private int OnDynamicHighlight()
        {
            try
            {
                if (!hover.ShouldPublish(null))
                {
                    return 0;
                }
                object preselected = hover.ResolvePreselected(attachedModel);
                if (preselected == null)
                {
                    return 0;
                }
                PublishHover(preselected);
            }
            catch (Exception exception)
            {
                log("hover publish failed: " + exception.Message);
            }
            return 0;
        }

        /// <summary>把预选对象写成 mode=hover 的上下文；已 Pin 时不覆盖。</summary>
        internal void PublishHover(object preselected)
        {
            Dictionary<string, object> state = bridge.ReadState();
            if (state.ContainsKey("pinned") && state["pinned"] != null)
            {
                return;
            }
            Dictionary<string, object> context = BuildContext(new[] { preselected }, null, "hover");
            if (context == null)
            {
                return;
            }
            bridge.WriteContext(context);
            currentContext = context;
        }

        // ------------------------------------------------------------------ 选择上下文

        /// <summary>采集当前选择并写入桥接目录；mode 为 pinned 时同时锁存 Pin。</summary>
        internal Dictionary<string, object> RefreshSelection(string mode)
        {
            if (attachedModel == null)
            {
                return null;
            }
            var objects = new List<object>();
            var typeIds = new List<int>();
            try
            {
                var selectionManager = attachedModel.SelectionManager as ISelectionMgr;
                if (selectionManager == null)
                {
                    return null;
                }
                int count = selectionManager.GetSelectedObjectCount2(-1);
                for (int index = 1; index <= count; index++)
                {
                    object entity = selectionManager.GetSelectedObject6(index, -1);
                    if (entity == null)
                    {
                        continue;
                    }
                    objects.Add(entity);
                    try
                    {
                        typeIds.Add(selectionManager.GetSelectedObjectType3(index, -1));
                    }
                    catch (Exception)
                    {
                        typeIds.Add(-1);
                    }
                }
            }
            catch (Exception exception)
            {
                log("read selection failed: " + exception.Message);
                return null;
            }

            entityCache.Clear();
            Dictionary<string, object> context = BuildContext(objects, typeIds, mode);
            if (context == null)
            {
                return null;
            }
            bridge.WriteContext(context);
            currentContext = context;
            if (mode == "pinned")
            {
                var document = (Dictionary<string, object>)context["document"];
                var selection = (Dictionary<string, object>)context["selection"];
                var tokens = new List<string>();
                foreach (Dictionary<string, object> entity in (IList<Dictionary<string, object>>)selection["objects"])
                {
                    tokens.Add(Convert.ToString(entity["entityToken"], CultureInfo.InvariantCulture));
                }
                bridge.SetPinned(
                    Convert.ToString(document["documentToken"], CultureInfo.InvariantCulture),
                    Convert.ToString(selection["selectionToken"], CultureInfo.InvariantCulture),
                    tokens);
            }
            return context;
        }

        /// <summary>由当前文档与实体列表构建 SelectionContext。</summary>
        private Dictionary<string, object> BuildContext(IList<object> objects, IList<int> typeIds, string mode)
        {
            if (attachedModel == null)
            {
                return null;
            }
            var entities = new List<Dictionary<string, object>>();
            string activeToken = null;
            for (int index = 0; index < objects.Count; index++)
            {
                int typeId = typeIds != null && index < typeIds.Count ? typeIds[index] : -1;
                Dictionary<string, object> entity = BuildEntity(objects[index], typeId);
                if (entity == null)
                {
                    continue;
                }
                entities.Add(entity);
                if (activeToken == null)
                {
                    activeToken = Convert.ToString(entity["entityToken"], CultureInfo.InvariantCulture);
                }
            }

            string path = SafeString(delegate { return attachedModel.GetPathName(); });
            string title = SafeString(delegate { return attachedModel.GetTitle(); });
            int documentTypeId = SafeInt(delegate { return attachedModel.GetType(); }, 1);
            string documentType = documentTypeId == 2 ? "Assembly" : (documentTypeId == 3 ? "Drawing" : "Part");
            string documentToken = PointPromptContract.DocumentToken(path, documentType);
            int revision = bridge.DocumentRevision(documentToken);
            bool dirty = SafeBool(delegate { return attachedModel.GetSaveFlag(); }, false);
            string configuration = SafeString(delegate
            {
                return ((IConfiguration)attachedModel.ConfigurationManager.ActiveConfiguration).Name;
            });
            Dictionary<string, object> state = bridge.ReadState();
            int bridgeRevision = 0;
            if (state.ContainsKey("bridgeRevision"))
            {
                int.TryParse(Convert.ToString(state["bridgeRevision"], CultureInfo.InvariantCulture), out bridgeRevision);
            }

            return PointPromptContract.BuildContext(
                documentToken,
                path,
                title,
                documentType,
                configuration,
                revision,
                revision,
                dirty,
                "UNKNOWN",
                entities,
                mode,
                activeToken,
                bridgeRevision,
                "addin");
        }

        /// <summary>把单个 COM 实体转为 DTO，并缓存 COM 引用供高亮使用。</summary>
        private Dictionary<string, object> BuildEntity(object entity, int typeId)
        {
            if (entity == null)
            {
                return null;
            }
            string entityType = MapSelectType(typeId);
            if (entityType == "Unknown")
            {
                entityType = DetectEntityType(entity);
            }
            string geometryType = "Unknown";
            var geometry = new Dictionary<string, object>();
            var evidence = new List<string>();
            string featureName = null;
            string featureType = null;
            string resolutionStatus = "unresolved";
            string resolutionMethod = "none";
            string resolutionReason = null;
            double confidence = 0.0;

            try
            {
                if (entityType == "Face")
                {
                    object surface = ((dynamic)entity).GetSurface();
                    if (surface != null)
                    {
                        dynamic surfaceDynamic = surface;
                        if (surfaceDynamic.IsCylinder)
                        {
                            geometryType = "Cylinder";
                            var parameters = (double[])surfaceDynamic.CylinderParams;
                            if (parameters != null && parameters.Length >= 7)
                            {
                                geometry["centerMm"] = new[] { Round(parameters[0] * 1000.0), Round(parameters[1] * 1000.0), Round(parameters[2] * 1000.0) };
                                geometry["axis"] = new[] { Round(parameters[3]), Round(parameters[4]), Round(parameters[5]) };
                                geometry["diameterMm"] = Round(parameters[6] * 2000.0);
                            }
                        }
                        else if (surfaceDynamic.IsPlane)
                        {
                            geometryType = "Planar";
                            var parameters = (double[])surfaceDynamic.PlaneParams;
                            if (parameters != null && parameters.Length >= 6)
                            {
                                geometry["centerMm"] = new[] { Round(parameters[0] * 1000.0), Round(parameters[1] * 1000.0), Round(parameters[2] * 1000.0) };
                                geometry["normal"] = new[] { Round(parameters[3]), Round(parameters[4]), Round(parameters[5]) };
                            }
                        }
                        else if (surfaceDynamic.IsCone)
                        {
                            geometryType = "Cone";
                        }
                        else if (surfaceDynamic.IsTorus)
                        {
                            geometryType = "Torus";
                        }
                        else if (surfaceDynamic.IsSphere)
                        {
                            geometryType = "Sphere";
                        }
                    }
                    object feature = null;
                    try
                    {
                        feature = ((dynamic)entity).GetFeature();
                    }
                    catch (Exception exception)
                    {
                        evidence.Add("GetFeature: " + exception.GetType().Name);
                    }
                    if (feature != null)
                    {
                        featureName = SafeString(delegate { return ((dynamic)feature).Name; });
                        featureType = SafeString(delegate { return ((dynamic)feature).GetTypeName2(); });
                        confidence = 0.9;
                        resolutionStatus = string.IsNullOrEmpty(featureName) ? "unresolved" : "resolved";
                        resolutionMethod = "face_get_feature";
                    }
                    else
                    {
                        resolutionReason = "无法从面追溯到所属特征；可能是导入实体";
                    }
                }
                else if (entityType == "Edge")
                {
                    object curve = ((dynamic)entity).GetCurve();
                    if (curve != null)
                    {
                        dynamic curveDynamic = curve;
                        if (curveDynamic.IsLine)
                        {
                            geometryType = "Line";
                        }
                        else if (curveDynamic.IsCircle)
                        {
                            geometryType = "Circle";
                            var parameters = (double[])curveDynamic.CircleParams;
                            if (parameters != null && parameters.Length >= 7)
                            {
                                geometry["centerMm"] = new[] { Round(parameters[0] * 1000.0), Round(parameters[1] * 1000.0), Round(parameters[2] * 1000.0) };
                                geometry["diameterMm"] = Round(parameters[6] * 2000.0);
                            }
                        }
                        else if (curveDynamic.IsSpline)
                        {
                            geometryType = "Spline";
                        }
                    }
                    resolutionMethod = "edge_adjacent_face";
                }
                else if (entityType == "Vertex")
                {
                    var point = (double[])((dynamic)entity).GetPoint();
                    if (point != null && point.Length >= 3)
                    {
                        geometry["centerMm"] = new[] { Round(point[0] * 1000.0), Round(point[1] * 1000.0), Round(point[2] * 1000.0) };
                    }
                    geometryType = "Point";
                    resolutionMethod = "selection_type";
                }
                else if (entityType == "Feature")
                {
                    featureName = SafeString(delegate { return ((dynamic)entity).Name; });
                    featureType = SafeString(delegate { return ((dynamic)entity).GetTypeName2(); });
                    confidence = 0.9;
                    resolutionStatus = "resolved";
                    resolutionMethod = "selection_type";
                }
                else if (entityType == "Component")
                {
                    geometryType = "Unknown";
                    resolutionMethod = "selection_type";
                }
            }
            catch (Exception exception)
            {
                evidence.Add("geometry: " + exception.GetType().Name + ": " + exception.Message);
            }

            if (resolutionStatus == "unresolved" && entityType == "Feature")
            {
                resolutionStatus = "resolved";
            }

            string persistentReference = null;
            try
            {
                object reference = attachedModel.Extension.GetPersistReference3(entity);
                var bytes = reference as byte[];
                if (bytes != null && bytes.Length > 0)
                {
                    persistentReference = Convert.ToBase64String(bytes);
                }
            }
            catch (Exception exception)
            {
                evidence.Add("GetPersistReference3: " + exception.GetType().Name);
            }

            Dictionary<string, object> dto = PointPromptContract.BuildEntity(
                entityType,
                geometryType,
                null,
                geometry,
                featureName,
                featureType,
                confidence,
                resolutionStatus,
                resolutionMethod,
                resolutionReason,
                evidence,
                persistentReference);

            string token = Convert.ToString(dto["entityToken"], CultureInfo.InvariantCulture);
            if (!entityCache.ContainsKey(token))
            {
                entityCache[token] = entity;
            }
            return dto;
        }

        /// <summary>按 token 取当前会话缓存的 COM 实体（高亮用）。</summary>
        internal object FindCachedEntity(string entityToken)
        {
            object entity;
            return entityCache.TryGetValue(entityToken ?? string.Empty, out entity) ? entity : null;
        }

        private static string MapSelectType(int typeId)
        {
            switch ((swSelectType_e)typeId)
            {
                case swSelectType_e.swSelFACES: return "Face";
                case swSelectType_e.swSelEDGES: return "Edge";
                case swSelectType_e.swSelVERTICES: return "Vertex";
                case swSelectType_e.swSelSKETCHES: return "Sketch";
                case swSelectType_e.swSelDIMENSIONS: return "Dimension";
                case swSelectType_e.swSelCOMPONENTS: return "Component";
                case swSelectType_e.swSelBODYFEATURES: return "Feature";
                case swSelectType_e.swSelDATUMPLANES: return "Feature";
                case swSelectType_e.swSelDATUMAXES: return "Feature";
                case swSelectType_e.swSelDATUMPOINTS: return "Feature";
                default: return "Unknown";
            }
        }

        private static double Round(double value)
        {
            return Math.Round(value, 4);
        }

        /// <summary>swSelectType 缺失时按接口指纹探测实体类型。</summary>
        private static string DetectEntityType(object entity)
        {
            if (entity == null)
            {
                return "Unknown";
            }
            try
            {
                ((dynamic)entity).GetSurface();
                return "Face";
            }
            catch (Exception)
            {
            }
            try
            {
                ((dynamic)entity).GetCurve();
                return "Edge";
            }
            catch (Exception)
            {
            }
            try
            {
                ((dynamic)entity).GetPoint();
                return "Vertex";
            }
            catch (Exception)
            {
            }
            return "Unknown";
        }

        private static string SafeString(Func<object> getter)
        {
            try
            {
                object value = getter();
                return value == null ? string.Empty : Convert.ToString(value, CultureInfo.InvariantCulture);
            }
            catch (Exception)
            {
                return string.Empty;
            }
        }

        private static int SafeInt(Func<object> getter, int fallback)
        {
            try
            {
                object value = getter();
                return value == null ? fallback : Convert.ToInt32(value, CultureInfo.InvariantCulture);
            }
            catch (Exception)
            {
                return fallback;
            }
        }

        private static bool SafeBool(Func<object> getter, bool fallback)
        {
            try
            {
                object value = getter();
                return value == null ? fallback : Convert.ToBoolean(value, CultureInfo.InvariantCulture);
            }
            catch (Exception)
            {
                return fallback;
            }
        }

        public void Dispose()
        {
            DetachDocumentEvents();
            entityCache.Clear();
        }
    }
}
