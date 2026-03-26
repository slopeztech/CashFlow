from django.contrib.auth.models import User

from core.gamification import evaluate_gamification_for_user
from core.models import Gamification, GamificationRewardCompletion


def achieved_gamification_rows(users=None, gamifications=None):
    users = users or User.objects.filter(is_staff=False).order_by('username')
    gamifications = gamifications or Gamification.objects.order_by('-start_at')

    rewarded_pairs = {
        (item.user_id, item.gamification_id): item
        for item in GamificationRewardCompletion.objects.select_related('rewarded_by')
    }

    rows = []
    for user in users:
        for gamification in gamifications:
            status = evaluate_gamification_for_user(gamification, user)
            if not status['achieved']:
                continue
            reward_completion = rewarded_pairs.get((user.id, gamification.id))
            rows.append(
                {
                    'user': user,
                    'gamification': gamification,
                    'status': status,
                    'reward_completed': reward_completion is not None,
                    'reward_completion': reward_completion,
                }
            )
    return rows
