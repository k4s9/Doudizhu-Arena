import { createRouter, createWebHistory } from 'vue-router';
import MatchList from '../views/MatchList.vue';
import MatchView from '../views/MatchView.vue';
import ReplayView from '../views/ReplayView.vue';
import CreateMatch from '../views/CreateMatch.vue';
import PlayerManager from '../views/PlayerManager.vue';
import PlayerDetail from '../views/PlayerDetail.vue';
import PlayerLeaderboard from '../views/PlayerLeaderboard.vue';
import ConfigManager from '../views/ConfigManager.vue';
import EvaluationList from '../views/EvaluationList.vue';
import EvaluationCreate from '../views/EvaluationCreate.vue';
import EvaluationDetail from '../views/EvaluationDetail.vue';

const routes = [
  { path: '/', component: MatchList },
  { path: '/create', component: CreateMatch },
  { path: '/players/leaderboard', component: PlayerLeaderboard },
  { path: '/players', component: PlayerManager },
  { path: '/players/:id', component: PlayerDetail, props: true },
  { path: '/configs', component: ConfigManager },
  { path: '/evaluations', component: EvaluationList },
  { path: '/evaluations/new', component: EvaluationCreate },
  { path: '/evaluations/:id', component: EvaluationDetail, props: true },
  { path: '/match/:id', component: MatchView, props: true },
  { path: '/replay/:matchId', component: ReplayView, props: true },
  { path: '/replay/:matchId/:handNum', component: ReplayView, props: true },
];

export default createRouter({
  history: createWebHistory(),
  routes,
});
