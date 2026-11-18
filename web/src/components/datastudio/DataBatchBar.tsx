import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { useDataBatchStore } from "@/store/dataBatchStore";
import { useProjectStore } from "@/store/projectStore";

/** 浮条占的高度（浮条本身 + 离底边 + 一点空隙）：浮条出现时清单滚动容器加这么多底部留白，最后一行才能滚到浮条上方 */
export const BATCH_BAR_RESERVE = "3.75rem";

/** 这一条此刻要不要出现（浮条自己与抽屉留白共用同一个判断） */
export function useBatchBarVisible(): boolean {
  const count = useDataBatchStore((s) => s.selection.length);
  const running = useDataBatchStore((s) => s.running);
  const stop = useDataBatchStore((s) => s.stop);
  const failed = useDataBatchStore((s) =>
    Object.values(s.rows).some((r) => r.state === "failed"),
  );
  return running || count >= 2 || failed || stop != null;
}

/**
 * 数据抽屉底部的一条（浮在清单区域底部、绝对定位不占位，出现 / 消失清单不跳）：多选 ≥ 2 份时出现，主按钮「各画一张」+ 灰字份数；进行中按钮禁用、写「正在画 2 / 3」；
 * 有没画成的（它们少于 2 份时这一条也在）按钮是「再试一次」，只重做没成的；项目文件夹写不进、整批停下时按钮是「换个文件夹」。
 * 少于 2 份又没有要说的，这一条不存在（单份走临时标签）。
 */
export function DataBatchBar() {
  const { t } = useTranslation("ai");
  const { t: tw } = useTranslation("workspace");
  const count = useDataBatchStore((s) => s.selection.length);
  const running = useDataBatchStore((s) => s.running);
  const progress = useDataBatchStore((s) => s.progress);
  const stop = useDataBatchStore((s) => s.stop);
  const failed = useDataBatchStore((s) =>
    Object.values(s.rows).some((r) => r.state === "failed"),
  );
  const visible = useBatchBarVisible();
  if (!visible) return null;
  const label = running
    ? t("dataFigureV2.batch.progress", {
        done: Math.min(progress.done + 1, progress.total),
        total: progress.total,
      })
    : stop === "unwritable"
      ? t("dataFigureV2.problem.otherFolder")
      : failed
        ? t("dataFigureV2.problem.retry")
        : t("dataFigureV2.batch.draw");
  return (
    <div
      data-studio-batch-bar
      className="pointer-events-none absolute inset-x-3 bottom-3 flex justify-center animate-rise-in motion-reduce:animate-none"
    >
      {/* 小浮条：与画布底部工具条同一套观感；窄到放不下时灰字换到被裁掉的第二行，等于省掉，按钮不换行、浮条不变高 */}
      <div className="pointer-events-auto flex h-10 max-w-full flex-wrap items-center gap-x-2 gap-y-4 overflow-hidden rounded-full bg-surface p-1 pr-3 shadow-pop">
        <Button
          variant="primary"
          size="md"
          className="shrink-0 whitespace-nowrap"
          data-studio-batch-run
          disabled={running}
          onClick={() =>
            stop === "unwritable"
              ? useProjectStore.getState().showPicker()
              : void useDataBatchStore.getState().run()
          }
        >
          {label}
        </Button>
        {count > 0 && (
          <span className="whitespace-nowrap text-xs text-ink-3 tabular-nums">
            {tw("dataDrawer.count", { count })}
          </span>
        )}
      </div>
    </div>
  );
}
