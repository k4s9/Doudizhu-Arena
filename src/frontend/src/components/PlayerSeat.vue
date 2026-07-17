<script setup>
/**
 * PlayerSeat — generic player info display (name, team, role, hand size).
 * Used as a base by BiddingSeat, PlayingSeat, LandlordSeat, and IdleSeat.
 *
 * This is now a thin reusable wrapper. Most logic moved to the specific seat components.
 */
import { computed } from 'vue';

const props = defineProps({
  seat: { type: String, default: '' },
  player: { type: Object, default: null },
  active: { type: Boolean, default: false },
  isLandlord: { type: Boolean, default: false },
  /** Show the hand size badge */
  showHandSize: { type: Boolean, default: true },
  /** Chinese seat label override */
  seatLabel: { type: String, default: '' },
});

const SEAT_NAMES = { S: '南', E: '东', N: '北', W: '西' };
const ROLE_NAMES = { landlord: '地主', farmer: '农民', idle: '闲家' };

const seatName = computed(() => props.seatLabel || SEAT_NAMES[props.seat] || props.seat);
const roleLabel = computed(() => ROLE_NAMES[props.player?.role] || '');
const teamLabel = computed(() => props.player?.team === 'red' ? '红队' : props.player?.team === 'blue' ? '蓝队' : '');
const teamColorClass = computed(() => {
  if (props.player?.team === 'red') return 'text-red-400';
  if (props.player?.team === 'blue') return 'text-blue-400';
  return 'text-slate-400';
});
</script>

<template>
  <div class="flex flex-col items-center gap-1">
    <!-- Seat label + role -->
    <div class="flex items-center gap-1.5">
      <span class="text-xs text-slate-500">{{ seatName }}</span>
      <span v-if="isLandlord" class="text-amber-400 text-sm">👑</span>
      <span
        v-if="roleLabel"
        :class="[
          'text-[10px] px-1.5 py-0.5 rounded-full font-semibold',
          player?.role === 'landlord' ? 'bg-amber-500/20 text-amber-400' :
          player?.role === 'farmer' ? 'bg-green-500/20 text-green-400' :
          'bg-slate-500/20 text-slate-400',
        ]"
      >
        {{ roleLabel }}
      </span>
    </div>

    <!-- Agent name -->
    <div :class="['text-sm font-semibold', teamColorClass]">
      {{ player?.agent_name || '—' }}
    </div>

    <!-- Team tag -->
    <div v-if="teamLabel" :class="['text-[10px]', teamColorClass]">
      {{ teamLabel }}
    </div>

    <!-- Hand size -->
    <div v-if="showHandSize && player" class="text-xs text-slate-500">
      {{ player.hand_size }} 张
    </div>
  </div>
</template>
