from __future__ import annotations


def install_scan_display_fix(main_window_class) -> None:
    """Po úspěšném průchodu vždy ukaž nalezené posty dole v tabulce.

    První průchod ukládá současné posty jako ``known``. Pokud byl zrovna
    aktivní jiný filtr nebo se při obnově tabulky profilu ztratila aktuální
    selekce, mohla spodní tabulka zůstat prázdná i po úspěšném načtení.
    """
    original_scan_finished = main_window_class._scan_finished

    def scan_finished_and_show_posts(self, posts: list) -> None:
        profile_id = self.scanning_profile_id
        original_scan_finished(self, posts)

        if profile_id is None:
            return

        # Po ručním „Projít profil“ chce uživatel vidět výsledek, ne prázdnou
        # tabulku kvůli filtru „Nové“. První průchod je navíc celý ``known``.
        self.post_filter.blockSignals(True)
        self.post_filter.setCurrentIndex(0)  # Všechny
        self.post_filter.blockSignals(False)

        self.select_profile(profile_id)
        self.refresh_posts()

        if posts and self.posts_table.rowCount() == 0:
            self.statusBar().showMessage(
                f"Načteno {len(posts)} příspěvků, ale tabulku se nepodařilo obnovit.",
                7000,
            )

    main_window_class._scan_finished = scan_finished_and_show_posts
