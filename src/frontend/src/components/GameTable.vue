<script setup>
import CardHand from './CardHand.vue';

const props = defineProps({
  table: { type: String, required: true }, // 'A' | 'B'
  tableData: { type: Object, default: null },
  showCards: { type: Boolean, default: true },
});

function roleLabel(role) {
  if (role === 'landlord') return '👑 地主';
  if (role === 'farmer') return '🌾 农民';
  return '👀 闲家';
}

function playerClass(seat) {
  switch (seat) {
    case 'S': return 'player-south';
    case 'E': return 'player-east';
    case 'N': return 'player-north';
    case 'W': return 'player-west';
    default: return '';
  }
}

function isLandlord(seat) {
  return props.tableData?.landlord === seat;
}
</script>

<template>
  <div class="bg-slate-800 rounded-xl border border-slate-700 p-4">
    <div class="flex items-center justify-between mb-3">
      <h3 class="text-lg font-semibold text-slate-200">
        Table {{ table }}
        <span v-if="tableData?.phase" class="text-xs text-slate-400 ml-2">— {{ tableData.phase }}</span>
      </h3>
      <div v-if="tableData?.dizhu_cards?.length" class="flex items-center gap-1 text-xs text-slate-400">
        <span>底牌:</span>
        <span v-for="(c, i) in tableData.dizhu_cards" :key="i" class="text-white font-mono">{{ c }}</span>
      </div>
    </div>

    <!-- Current trick -->
    <div v-if="tableData?.current_pattern" class="text-center mb-3 text-sm text-amber-400">
      Current: {{ tableData.current_pattern.pattern }}
      <span v-if="tableData.current_pattern.max_rank">
        (max rank: {{ tableData.current_pattern.max_rank }})
      </span>
    </div>

    <!-- Players grid (landlord-centric layout) -->
    <div class="grid grid-cols-3 gap-2">
      <!-- North (E) — top-right -->
      <div></div>
      <div
        :class="[
          'rounded-lg p-2 text-center border',
          tableData?.current_seat === 'E' ? 'border-amber-400 bg-amber-400/10' : 'border-slate-600 bg-slate-800/50',
        ]"
      >
        <div class="text-xs font-semibold mb-1">
          {{ roleLabel(tableData?.players?.E?.role) }}
          <span v-if="isLandlord('E')" class="text-amber-400">👑</span>
        </div>
        <div class="text-xs text-slate-400">{{ tableData?.players?.E?.agent_name }}</div>
        <div v-if="tableData?.players?.E" class="text-xs text-slate-500">
          {{ tableData.players.E.hand_size }} cards
        </div>
        <CardHand
          v-if="showCards && tableData?.players?.E"
          :cards="tableData.players.E.hand_cards || []"
          size="sm"
          :face-down="true"
        />
      </div>
      <div></div>

      <!-- West (W) — left -->
      <div
        :class="[
          'rounded-lg p-2 text-center border',
          tableData?.current_seat === 'W' ? 'border-amber-400 bg-amber-400/10' : 'border-slate-600 bg-slate-800/50',
        ]"
      >
        <div class="text-xs font-semibold mb-1">
          {{ roleLabel(tableData?.players?.W?.role) }}
          <span v-if="isLandlord('W')" class="text-amber-400">👑</span>
        </div>
        <div class="text-xs text-slate-400">{{ tableData?.players?.W?.agent_name }}</div>
        <div v-if="tableData?.players?.W" class="text-xs text-slate-500">
          {{ tableData.players.W.hand_size }} cards
        </div>
        <CardHand
          v-if="showCards && tableData?.players?.W"
          :cards="tableData.players.W.hand_cards || []"
          size="sm"
          :face-down="true"
        />
      </div>

      <!-- Center — dealer / current pattern info -->
      <div class="flex items-center justify-center">
        <div class="text-xs text-slate-500 text-center">
          <div v-if="tableData?.dealer">Dealer: {{ tableData.dealer }}</div>
          <div v-if="tableData?.landlord">Landlord: {{ tableData.landlord }}</div>
        </div>
      </div>

      <!-- East (E) — right -->
      <div
        :class="[
          'rounded-lg p-2 text-center border',
          tableData?.current_seat === 'N' ? 'border-amber-400 bg-amber-400/10' : 'border-slate-600 bg-slate-800/50',
        ]"
      >
        <div class="text-xs font-semibold mb-1">
          {{ roleLabel(tableData?.players?.N?.role) }}
          <span v-if="isLandlord('N')" class="text-amber-400">👑</span>
        </div>
        <div class="text-xs text-slate-400">{{ tableData?.players?.N?.agent_name }}</div>
        <div v-if="tableData?.players?.N" class="text-xs text-slate-500">
          {{ tableData.players.N.hand_size }} cards
        </div>
        <CardHand
          v-if="showCards && tableData?.players?.N"
          :cards="tableData.players.N.hand_cards || []"
          size="sm"
          :face-down="true"
        />
      </div>

      <!-- South (S) — bottom center (landlord, main view) -->
      <div></div>
      <div
        :class="[
          'rounded-lg p-2 text-center border',
          tableData?.current_seat === 'S' ? 'border-amber-400 bg-amber-400/10' : 'border-slate-600 bg-slate-800/50',
        ]"
      >
        <div class="text-xs font-semibold mb-1">
          {{ roleLabel(tableData?.players?.S?.role) }}
          <span v-if="isLandlord('S')" class="text-amber-400">👑</span>
        </div>
        <div class="text-xs text-slate-400">{{ tableData?.players?.S?.agent_name }}</div>
        <div v-if="tableData?.players?.S" class="text-xs text-slate-500">
          {{ tableData.players.S.hand_size }} cards
        </div>
        <CardHand
          v-if="showCards && tableData?.players?.S?.hand_cards"
          :cards="tableData.players.S.hand_cards"
          size="sm"
          :highlight="tableData?.current_seat === 'S'"
        />
        <CardHand
          v-else-if="showCards && tableData?.players?.S"
          :cards="[]"
          size="sm"
          :face-down="true"
        />
      </div>
      <div></div>
    </div>
  </div>
</template>
