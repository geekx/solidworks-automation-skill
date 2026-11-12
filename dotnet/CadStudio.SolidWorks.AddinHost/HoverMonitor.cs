using System;
using System.Diagnostics;
using SolidWorks.Interop.sldworks;

namespace CadStudio.SolidWorks.AddinHost
{
    /// <summary>
    /// 图形区 Hover 预选：只负责「什么时候可以发布 hover」与「预选对象是谁」。
    ///
    /// 事件入口是文档级 `DynamicHighlightNotify`；实体读取顺序为
    /// `ISelectionMgr.GetPreSelectedObject()` → `GetSelectedObject6(-1, -1)`。
    /// 两者都不可用时返回 null，由 TaskPane 显示 hover 降级状态。
    /// </summary>
    internal sealed class HoverMonitor
    {
        private readonly Stopwatch throttle = Stopwatch.StartNew();
        private string lastToken;

        internal bool Enabled { get; set; } = true;

        /// <summary>节流并去重：同一实体在 120 ms 内只发布一次。</summary>
        internal bool ShouldPublish(string entityToken, int intervalMs = 120)
        {
            if (!Enabled)
            {
                return false;
            }
            if (throttle.ElapsedMilliseconds < intervalMs && entityToken == lastToken)
            {
                return false;
            }
            if (throttle.ElapsedMilliseconds < intervalMs)
            {
                return false;
            }
            throttle.Restart();
            lastToken = entityToken ?? string.Empty;
            return true;
        }

        /// <summary>从文档读取当前预选对象；失败返回 null。</summary>
        internal object ResolvePreselected(ModelDoc2 model)
        {
            if (model == null)
            {
                return null;
            }
            try
            {
                var selectionManager = model.SelectionManager as ISelectionMgr;
                if (selectionManager == null)
                {
                    return null;
                }
                try
                {
                    object preselected = selectionManager.GetPreSelectedObject();
                    if (preselected != null)
                    {
                        return preselected;
                    }
                }
                catch (Exception)
                {
                    // 某些文档类型在非预选阶段会拒绝该调用，继续走动态高亮对象。
                }
                try
                {
                    return selectionManager.GetSelectedObject6(-1, -1);
                }
                catch (Exception)
                {
                    return null;
                }
            }
            catch (Exception)
            {
                return null;
            }
        }
    }
}
