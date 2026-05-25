# src/ethics_podcast/bgm_manager.py

import asyncio
from pathlib import Path

import pygame


def get_project_root() -> Path:
    """
    Return the Reachy project root.

    This file is here:
        Reachy/src/ethics_podcast/bgm_manager.py

    parents[0] = ethics_podcast
    parents[1] = src
    parents[2] = Reachy
    """
    return Path(__file__).resolve().parents[2]


def get_bgm_path_for_session(session_name: str | None) -> Path:
    """
    Pick one BGM file for each video session.

    Music folder:
        Reachy/music/
    """
    root = get_project_root()
    music_dir = root / "music"

    bgm_map = {
        "video_achievement": music_dir / "Circuit.mp3",
        "video_environment": music_dir / "Deep Tones.mp3",
        "video_military": music_dir / "Driving Concern.mp3",
        "video_relationship": music_dir / "Bit Bit Loop.mp3",
    }

    return bgm_map.get(session_name, music_dir / "Circuit.mp3")


class BGMManager:
    def __init__(
        self,
        music_path: str | Path,
        normal_volume: float = 0.20,
        speaking_volume: float = 0.045,
        fade_step_delay: float = 0.03,
        channel_id: int = 7,
    ):
        self.music_path = Path(music_path)
        self.normal_volume = normal_volume
        self.speaking_volume = speaking_volume
        self.fade_step_delay = fade_step_delay
        self.channel_id = channel_id

        self.current_volume = 0.0
        self._started = False
        self._fade_task: asyncio.Task | None = None

        self._sound: pygame.mixer.Sound | None = None
        self._channel: pygame.mixer.Channel | None = None

    def start(self) -> None:
        """
        Start BGM at volume 0.

        Important:
        This uses a dedicated pygame Channel, not pygame.mixer.music.
        That lets us force left and right volume to be equal.
        """
        if self._started:
            return

        if not self.music_path.exists():
            raise FileNotFoundError(f"BGM file not found: {self.music_path}")

        if not pygame.mixer.get_init():
            pygame.mixer.init()

        # Make sure enough mixer channels exist.
        pygame.mixer.set_num_channels(max(8, self.channel_id + 1))

        self._sound = pygame.mixer.Sound(str(self.music_path))
        self._channel = pygame.mixer.Channel(self.channel_id)

        # Force BGM to start centered.
        self._channel.set_volume(0.0, 0.0)
        self._channel.play(self._sound, loops=-1)

        self.current_volume = 0.0
        self._started = True

        print(f"  BGM started centered: {self.music_path}")

    def _set_center_volume(self, volume: float) -> None:
        """
        Set BGM volume equally to left and right.

        This prevents the BGM from following the current speaker's pan.
        """
        volume = max(0.0, min(1.0, volume))

        if self._channel is not None:
            self._channel.set_volume(volume, volume)

        self.current_volume = volume

    async def fade_to(self, target_volume: float, duration: float) -> None:
        """
        Smoothly change centered BGM volume.
        """
        if not self._started:
            return

        target_volume = max(0.0, min(1.0, target_volume))

        if self._fade_task and not self._fade_task.done():
            self._fade_task.cancel()

        self._fade_task = asyncio.create_task(
            self._fade(target_volume, duration)
        )

        try:
            await self._fade_task
        except asyncio.CancelledError:
            pass

    async def _fade(self, target_volume: float, duration: float) -> None:
        start_volume = self.current_volume
        steps = max(1, int(duration / self.fade_step_delay))

        for i in range(steps):
            ratio = (i + 1) / steps
            volume = start_volume + (target_volume - start_volume) * ratio

            self._set_center_volume(volume)

            await asyncio.sleep(self.fade_step_delay)

    async def intro(self) -> None:
        """
        Start BGM and slowly raise volume.
        """
        self.start()
        await self.fade_to(self.normal_volume, duration=2.0)

    async def lower_for_speaking(self) -> None:
        """
        Kept for compatibility.

        In the current design, we do not change volume every turn.
        """
        await self.fade_to(self.speaking_volume, duration=0.45)

    async def raise_after_speaking(self) -> None:
        """
        Kept for compatibility.

        In the current design, we do not change volume every turn.
        """
        await self.fade_to(self.normal_volume, duration=0.8)

    async def stop(self) -> None:
        """
        Fade out and stop BGM.
        """
        if not self._started:
            return

        await self.fade_to(0.0, duration=1.5)

        if self._channel is not None:
            self._channel.stop()

        self._started = False

        print("  BGM stopped")