<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import { useRouter } from "vue-router";
import {
  BellRing,
  BookOpenCheck,
  CheckCircle2,
  KeyRound,
  MapPin,
  PauseCircle,
  Utensils,
  X,
} from "lucide-vue-next";

const props = defineProps<{
  open: boolean;
  discoveryOnly?: boolean;
}>();

const emit = defineEmits<{
  close: [];
}>();

const router = useRouter();
const dialogRef = ref<HTMLDialogElement | null>(null);

watch(
  () => props.open,
  async (isOpen) => {
    await nextTick();
    const el = dialogRef.value;
    if (!el) return;
    if (isOpen && !el.open) {
      el.showModal();
    } else if (!isOpen && el.open) {
      el.close();
    }
  },
  { immediate: true },
);

function closeGuide() {
  if (dialogRef.value?.open) dialogRef.value.close();
  emit("close");
}

function onBackdropClick(event: MouseEvent) {
  if (event.target === dialogRef.value) closeGuide();
}

function jumpTo(path: string) {
  closeGuide();
  void router.push(path);
}
</script>

<template>
  <dialog
    ref="dialogRef"
    class="m-quick-guide"
    aria-label="商家摆摊快速使用说明"
    @close="emit('close')"
    @click="onBackdropClick"
  >
    <div class="m-guide-sheet">
      <header class="m-guide-header">
        <div>
          <span class="m-guide-eyebrow">一分钟上手</span>
          <h2><BookOpenCheck :size="21" /> 摆摊使用说明</h2>
        </div>
        <button
          type="button"
          class="m-guide-close"
          aria-label="关闭使用说明"
          @click="closeGuide"
        >
          <X :size="20" />
        </button>
      </header>

      <div class="m-guide-body">
        <p class="m-guide-lead">
          专为手机单手操作设计，记住下面 <strong>{{ discoveryOnly ? "3" : "4" }} 步</strong>就能顺畅出摊：
        </p>

        <ol class="m-guide-steps">
          <li>
            <span class="m-guide-num"><MapPin :size="18" /></span>
            <div>
              <strong>1. 出摊先点「确认位置」</strong>
              <p>
                到了摆摊点先点顶部<strong>开始营业</strong>。为防止学生跑空，系统每
                60 分钟需要顺手点一次确认位置；挪位置时点「更换位置」即可。
              </p>
            </div>
          </li>

          <li v-if="!discoveryOnly">
            <span class="m-guide-num"><BellRing :size="18" /></span>
            <div>
              <strong>2. 顾客先付款自动进「制作中」，做好点「做好了」</strong>
              <p>
                所有线上单必须<strong>先完成微信预付</strong>才会进接单台，杜绝跑单！默认开启<strong>付款后自动接单</strong>，您照单做完点大按钮<strong>做好了</strong>即可。建议在「更多」里打开新单声音提醒。
              </p>
            </div>
          </li>

          <li v-if="!discoveryOnly">
            <span class="m-guide-num"><KeyRound :size="18" /></span>
            <div>
              <strong>3. 零负担交餐结算：不用点收款，不怕忙忘点</strong>
              <p>
                货款已由微信自动核验！交餐时顺手点卡片上的<strong>已交餐 · 完成</strong>（或输 4 位取餐码核销）；万一忙得没腾出手，<strong>同学点「我已取到餐点」或出餐满 2 小时无争议</strong>也会自动完结并分账结算！
              </p>
            </div>
          </li>

          <li>
            <span class="m-guide-num"><PauseCircle :size="18" /></span>
            <div>
              <strong>{{ discoveryOnly ? "2. 菜品卖完随手改状态" : "4. 忙不过来？一键暂停" }}</strong>
              <p v-if="discoveryOnly">
                在底部「菜品」里可随时把单品标为<strong>今天有 / 卖完了 / 暂时不卖</strong>；收摊时点「收摊」，避免同学白跑一趟。
              </p>
              <p v-else>
                现场排队太长时，点顶部<strong>暂停接单</strong>（不影响手头已接订单）；某道菜卖完了，去底部「菜品」点<strong>暂停供应</strong>，随时能恢复。
              </p>
            </div>
          </li>

          <li v-if="discoveryOnly">
            <span class="m-guide-num"><CheckCircle2 :size="18" /></span>
            <div>
              <strong>3. 想开通线上预订？</strong>
              <p>
                当前为找摊展示模式，同学可在地图看到您的位置和菜品。如需开通线上点单自取，请联系校园运营团队核验开通。
              </p>
            </div>
          </li>
        </ol>

        <div class="m-guide-shortcuts" aria-label="常用设置直达">
          <button type="button" @click="jumpTo('/merchant/store#location')">
            <MapPin :size="16" /> 设置位置与营业时间
          </button>
          <button type="button" @click="jumpTo('/merchant/products')">
            <Utensils :size="16" /> 管理菜品与份数
          </button>
          <button
            v-if="!discoveryOnly"
            type="button"
            @click="jumpTo('/merchant/more#notifications')"
          >
            <BellRing :size="16" /> 开启新订单声音
          </button>
        </div>
      </div>

      <footer class="m-guide-footer">
        <button type="button" class="m-guide-done" @click="closeGuide">
          我知道了，开始忙吧
        </button>
      </footer>
    </div>
  </dialog>
</template>

<style scoped>
.m-quick-guide {
  border: 0;
  padding: 0;
  margin: auto;
  width: min(520px, calc(100vw - 24px));
  max-height: min(88dvh, 760px);
  border-radius: 22px;
  background: #fffdf8;
  color: #3a2d22;
  box-shadow: 0 24px 60px rgba(42, 27, 14, 0.28);
  overflow: hidden;
}
.m-quick-guide::backdrop {
  background: rgba(38, 26, 16, 0.52);
  backdrop-filter: blur(3px);
}
.m-guide-sheet {
  display: flex;
  flex-direction: column;
  max-height: min(88dvh, 760px);
}
.m-guide-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 20px 14px;
  border-bottom: 1px solid #efe2cf;
  background: linear-gradient(155deg, #fff8eb, #fffdf9);
}
.m-guide-eyebrow {
  display: block;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1.4px;
  color: #b55523;
  margin-bottom: 3px;
}
.m-guide-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 800;
  display: flex;
  align-items: center;
  gap: 8px;
  color: #34261b;
}
.m-guide-close {
  min-width: 44px;
  min-height: 44px;
  border-radius: 50%;
  border: 1px solid #e7d6c0;
  background: #fff9f0;
  color: #7c6047;
  display: grid;
  place-items: center;
  cursor: pointer;
}
.m-guide-body {
  padding: 16px 20px;
  overflow-y: auto;
  flex: 1;
}
.m-guide-lead {
  margin: 0 0 14px;
  font-size: 14px;
  line-height: 1.65;
  color: #6b533e;
}
.m-guide-steps {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 11px;
}
.m-guide-steps li {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  gap: 11px;
  padding: 13px 14px;
  border-radius: 15px;
  background: #fff9ef;
  border: 1px solid #eedcc4;
}
.m-guide-num {
  width: 36px;
  height: 36px;
  border-radius: 11px;
  background: #fdebd8;
  color: #b55523;
  display: grid;
  place-items: center;
  flex-shrink: 0;
}
.m-guide-steps strong {
  font-size: 15px;
  color: #36271c;
}
.m-guide-steps li > div > strong {
  display: block;
  font-size: 16px;
  margin-bottom: 4px;
}
.m-guide-steps p {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.68;
  color: #644d39;
}
.m-guide-shortcuts {
  margin-top: 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.m-guide-shortcuts button {
  flex: 1 1 140px;
  min-height: 44px;
  padding: 8px 12px;
  border-radius: 11px;
  border: 1px solid #e3cdb1;
  background: #fffdf9;
  color: #6d4c31;
  font-size: 13px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  cursor: pointer;
}
.m-guide-footer {
  padding: 12px 20px calc(14px + env(safe-area-inset-bottom));
  border-top: 1px solid #efe2cf;
  background: #fffdf8;
}
.m-guide-done {
  width: 100%;
  min-height: 48px;
  border: 0;
  border-radius: 13px;
  background: #b55523;
  color: #fff;
  font-size: 16px;
  font-weight: 800;
  cursor: pointer;
}
@media (max-width: 600px) {
  .m-quick-guide {
    margin: auto 0 0;
    width: 100%;
    max-width: 100%;
    border-radius: 22px 22px 0 0;
    max-height: 90dvh;
  }
  .m-guide-sheet {
    max-height: 90dvh;
  }
}
</style>
