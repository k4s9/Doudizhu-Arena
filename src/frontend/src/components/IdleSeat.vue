<script setup>
/**
 * IdleSeat — displays the idle (bystander) player during the bidding phase.
 * Shows only player info, no hand cards (they haven't been dealt cards).
 */
import PlayerSeat from './PlayerSeat.vue';

defineProps({
  seat: { type: String, required: true },
  player: { type: Object, default: null },
});
</script>

<template>
  <div class="bg-slate-800/60 rounded-lg border border-dashed border-slate-600 p-3 text-center min-w-[100px]">
    <PlayerSeat
      :seat="seat"
      :player="player"
      :show-hand-size="false"
    />
    <div class="text-[10px] text-slate-500 mt-1">未发牌</div>
  </div>
</template>
