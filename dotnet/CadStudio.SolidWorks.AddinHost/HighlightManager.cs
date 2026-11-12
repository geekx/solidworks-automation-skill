using System;
using System.Collections.Generic;
using System.Globalization;

namespace CadStudio.SolidWorks.AddinHost
{
    /// <summary>
    /// 画布高亮反馈：按 MCP 写入的 entityToken 命中当前会话缓存的 COM 实体并选中它。
    ///
    /// 说明：COM 没有「临时高亮但不选中」的公开接口，因此这里用原生选择作为可见反馈；
    /// 命不中的 token（模型已重建/换文档）只记录失败，绝不按相似性猜测目标。
    /// </summary>
    internal sealed class HighlightManager
    {
        private readonly Action<string> log;

        internal HighlightManager(Action<string> log)
        {
            this.log = log ?? delegate { };
        }

        /// <summary>对给定 token 列表执行高亮；返回命中数量。</summary>
        internal int Apply(IList<string> entityTokens, SelectionManager selection)
        {
            if (entityTokens == null || entityTokens.Count == 0 || selection == null)
            {
                return 0;
            }
            int applied = 0;
            foreach (string token in entityTokens)
            {
                object entity = selection.FindCachedEntity(token);
                if (entity == null)
                {
                    continue;
                }
                try
                {
                    // IFace2/IEdge2 等实体都有 Select4(Append, Data)；用 dynamic 避免逐个接口强转。
                    object result = ((dynamic)entity).Select4(true, null);
                    bool selected = result is bool ? (bool)result : true;
                    if (selected)
                    {
                        applied++;
                    }
                }
                catch (Exception exception)
                {
                    log("highlight failed for " + Convert.ToString(token, CultureInfo.InvariantCulture) + ": " + exception.Message);
                }
            }
            return applied;
        }
    }
}
